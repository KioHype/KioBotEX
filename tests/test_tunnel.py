"""Testes do túnel automático (sem internet, com um cloudflared de mentira)."""
import asyncio
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["KIOBOT_CLOUDFLARED"] = str(HERE / "fake_cloudflared.py")
import protocol as P
import relay
import tunnel

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FALHOU ") + name)


async def main():
    # ---- leitura do endereço no log
    m = tunnel.URL_RE.search('INF |  https://adverse-lucky-monkey-guide.trycloudflare.com  |')
    check("encontra o endereço do túnel no log", m and m.group(0) == "https://adverse-lucky-monkey-guide.trycloudflare.com")
    check("ignora api.trycloudflare.com (mensagem de erro)",
          tunnel.URL_RE.search('Post "https://api.trycloudflare.com/tunnel": no such host') is None)

    # ---- ciclo de vida
    os.environ["FAKE_MODE"] = "ok"
    t = tunnel.Tunnel(8765)
    url = await t.start(timeout=10)
    check("túnel abre e devolve o endereço público", url == "https://test-fake-words-here.trycloudflare.com")
    check("túnel registra o processo (para limpar depois de um fechamento forçado)", tunnel._pid_file().exists())
    await t.stop()
    check("parar o túnel encerra o processo", t.proc.returncode is not None)
    check("parar o túnel apaga o registro do processo", not tunnel._pid_file().exists())

    os.environ["FAKE_MODE"] = "crash"
    try:
        await tunnel.Tunnel(8765).start(timeout=10)
        check("túnel que falha ao abrir é reportado", False)
    except tunnel.TunnelError as ex:
        check("túnel que falha ao abrir é reportado", "fechou antes" in str(ex))

    os.environ["FAKE_MODE"] = "hang"
    t0 = asyncio.get_event_loop().time()
    try:
        await tunnel.Tunnel(8765).start(timeout=2)
        check("túnel que não responde desiste no prazo", False)
    except tunnel.TunnelError as ex:
        check("túnel que não responde desiste no prazo", "demorou" in str(ex) and asyncio.get_event_loop().time() - t0 < 8)

    # ---- sobras de um fechamento forçado
    os.environ["FAKE_MODE"] = "ok"
    t = tunnel.Tunnel(8765)
    await t.start(timeout=10)
    tunnel.kill_stale()      # o arquivo de registro é apagado; o processo falso não se chama cloudflared
    check("limpeza de túnel antigo não derruba processos de outros programas", t.proc.returncode is None)
    await t.stop()

    # ---- download conferido por hash
    good = b"MZ" + os.urandom(1000)

    class FakeResp:
        def __init__(self, data): self.data, self.headers = data, {"Content-Length": str(len(data))}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return "https://objects.example/x"
        def read(self, n):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

    real = tunnel.urllib.request.urlopen
    tmp = HERE / "dl.part"
    try:
        tunnel.urllib.request.urlopen = lambda *a, **k: FakeResp(good)
        tunnel._download("https://x", tmp, hashlib.sha256(good).hexdigest(), None)
        check("download com hash correto é aceito", tmp.read_bytes() == good)
        try:
            tunnel._download("https://x", tmp, "0" * 64, None)
            check("download adulterado é recusado", False)
        except tunnel.TunnelError:
            check("download adulterado é recusado", True)
    finally:
        tunnel.urllib.request.urlopen = real
        tmp.unlink(missing_ok=True)
    check("versão fixa tem hash para Windows e Linux",
          all(len(v[2]) == 64 for v in tunnel.BINARIES.values()) and ("Windows", "AMD64") in tunnel.BINARIES)

    # ---- IP real atrás da Cloudflare
    class Hdr(dict):
        def get(self, k, d=None):
            return super().get(k.lower(), d)
    mk = lambda h: SimpleNamespace(request=SimpleNamespace(headers=Hdr({k.lower(): v for k, v in h.items()})),
                                   remote_address=("127.0.0.1", 5555))
    proxied = relay.RelayServer(trust_proxy=True)
    check("atrás do proxy usa CF-Connecting-IP", proxied._ip(mk({"CF-Connecting-IP": "203.0.113.7"})) == "203.0.113.7")
    check("X-Forwarded-For forjado pelo cliente não engana (vale o último valor)",
          proxied._ip(mk({"X-Forwarded-For": "1.1.1.1, 203.0.113.9"})) == "203.0.113.9")
    check("sem proxy confiável ignora cabeçalhos", relay.RelayServer()._ip(mk({"CF-Connecting-IP": "9.9.9.9"})) == "127.0.0.1")

    # ---- convites
    check("convite sem esquema para domínio vira wss",
          P.parse_invite("adverse-lucky.trycloudflare.com/abcd2345") == ("wss://adverse-lucky.trycloudflare.com", "ABCD2345"))
    check("convite sem esquema para IP vira ws",
          P.parse_invite("192.168.0.10:8765/ABCD2345") == ("ws://192.168.0.10:8765", "ABCD2345"))
    check("endereço do relé sem esquema: domínio wss, IP ws",
          P.normalize_relay_url("meu.exemplo.com") == "wss://meu.exemplo.com"
          and P.normalize_relay_url("10.0.0.5:8765") == "ws://10.0.0.5:8765")

    # ---- sobra de teste manual nas configurações
    import json, tempfile
    from store import Settings
    cfg = Path(tempfile.mkdtemp()) / "settings.json"
    cfg.write_text(json.dumps({"relay_url": "wss://velho-teste.trycloudflare.com", "nick": "Kio"}))
    st = Settings(cfg)
    check("endereço temporário antigo é limpo ao abrir o programa", st["relay_url"] == "")
    check("a limpeza é gravada e o resto das configurações é mantido",
          json.loads(cfg.read_text())["relay_url"] == "" and st["nick"] == "Kio")
    cfg.write_text(json.dumps({"relay_url": "wss://meu-rele.exemplo.com"}))
    check("relé próprio de verdade é preservado", Settings(cfg)["relay_url"] == "wss://meu-rele.exemplo.com")

    print(f"\n{len(PASS)} ok, {len(FAIL)} falharam")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
