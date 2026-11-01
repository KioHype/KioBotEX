"""Conexão automática com a internet para salas do host.

O host não precisa configurar nada: o programa baixa uma vez o `cloudflared` oficial da Cloudflare
(versão fixa, conferida por SHA-256), abre um túnel gratuito para o relé embutido e usa o endereço
público gerado no convite. Túneis sem conta da Cloudflare não têm garantia de funcionamento.
"""
import asyncio
import hashlib
import os
import platform
import re
import subprocess
import sys
import urllib.request
from collections import deque
from pathlib import Path

from store import app_dir

CLOUDFLARED_VERSION = "2026.10.7"
_BASE = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/"

# (sistema, arquitetura) -> (arquivo, tamanho em MB, SHA-256)
BINARIES = {
    ("Windows", "AMD64"): ("cloudflared-windows-amd64.exe", 55,
                           "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343"),
    ("Linux", "x86_64"): ("cloudflared-linux-amd64", 40,
                          "1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872"),
}
# O (?!api\.) evita confundir a mensagem de erro "https://api.trycloudflare.com/tunnel" com a sala.
URL_RE = re.compile(r"https://(?!api\.)[a-z0-9][a-z0-9\-]*\.trycloudflare\.com")


class TunnelError(Exception):
    pass


def _override():
    return os.environ.get("KIOBOT_CLOUDFLARED")  # usado só nos testes


def _spec():
    return BINARIES.get((platform.system(), platform.machine()))


def supported() -> bool:
    return bool(_override()) or _spec() is not None


def size_mb() -> int:
    s = _spec()
    return s[1] if s else 0


def binary_path() -> Path:
    if _override():
        return Path(_override())
    return app_dir() / "bin" / _spec()[0]


def is_installed() -> bool:
    return binary_path().is_file()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_installed() -> bool:
    """Confere se o executável guardado é exatamente o oficial (nada foi trocado ou corrompido)."""
    if _override():
        return True
    spec = _spec()
    return bool(spec) and is_installed() and _sha256(binary_path()) == spec[2]


def _download(url, tmp: Path, expected: str, progress):
    h, done = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "KioBotEX"})
    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
        if not r.geturl().startswith("https://"):
            raise TunnelError("Download recusado: o endereço não é seguro.")
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if h.hexdigest() != expected:
        raise TunnelError("O arquivo baixado não confere com o original e foi descartado.")


async def install(progress=None):
    spec = _spec()
    if not spec:
        raise TunnelError("Seu sistema ainda não é suportado para salas pela internet automáticas. "
                          "Use \"mesma rede\" ou um relé próprio nas Configurações.")
    name, _, sha = spec
    dest = binary_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        await asyncio.to_thread(_download, _BASE + name, tmp, sha, progress)
        if os.name != "nt":
            tmp.chmod(0o755)
        tmp.replace(dest)
    except TunnelError:
        raise
    except Exception as ex:
        raise TunnelError("Não consegui baixar o componente de internet. Verifique sua conexão "
                          f"e tente de novo. ({type(ex).__name__})")
    finally:
        tmp.unlink(missing_ok=True)


# ---------- processo ----------
def _pid_file() -> Path:
    return app_dir() / "tunnel.pid"


def kill_stale():
    """Se o programa foi fechado à força, o túnel anterior pode ter ficado vivo. Encerra só ele."""
    try:
        pid = int(_pid_file().read_text().strip())
    except (OSError, ValueError):
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid), "/FI", "IMAGENAME eq cloudflared*"],
                           capture_output=True, creationflags=0x08000000, timeout=10)
        else:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if comm.startswith("cloudflared"):
                os.kill(pid, 15)
    except Exception:
        pass
    _pid_file().unlink(missing_ok=True)


class Tunnel:
    def __init__(self, port, emit=None):
        self.port, self.emit = port, emit
        self.proc = None
        self._url = None
        self._reader = None
        self._stopping = False
        self._tail = deque(maxlen=12)

    def _command(self):
        exe = str(binary_path())
        base = [sys.executable, exe] if exe.endswith(".py") else [exe]
        return base + ["tunnel", "--url", f"http://127.0.0.1:{self.port}", "--no-autoupdate"]

    async def start(self, timeout=45) -> str:
        """Abre o túnel e retorna o endereço público (https://...)."""
        kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}   # sem janela preta
        self._url = asyncio.get_running_loop().create_future()
        self.proc = await asyncio.create_subprocess_exec(
            *self._command(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)
        try:
            _pid_file().write_text(str(self.proc.pid))
        except OSError:
            pass
        self._reader = asyncio.ensure_future(self._read())
        try:
            return await asyncio.wait_for(asyncio.shield(self._url), timeout)
        except asyncio.TimeoutError:
            await self.stop()
            raise TunnelError("A conexão com a internet demorou demais para abrir. Verifique sua "
                              "internet (alguns Wi-Fi de empresa ou escola bloqueiam isso) ou use \"mesma rede\".")
        except TunnelError:
            await self.stop()
            raise

    async def _read(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            self._tail.append(text)
            m = URL_RE.search(text)
            if m and not self._url.done():
                self._url.set_result(m.group(0))
        if not self._url.done():
            if self._stopping:
                self._url.cancel()
            else:
                self._url.set_exception(TunnelError(
                    "A conexão com a internet fechou antes de ficar pronta. "
                    + (self._tail[-1][:200] if self._tail else "")))
        elif not self._stopping and self.emit:
            self.emit("tunnel_down", {})

    async def stop(self):
        self._stopping = True
        p = self.proc
        if p and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), 3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        if self._reader:
            self._reader.cancel()
        _pid_file().unlink(missing_ok=True)
