"""Rede do KioBot EX: sessão do host e sessão do convidado.

Tudo aqui é asyncio puro (sem Qt). A interface recebe eventos pelo callback `emit(nome, dados)`.

Ideia central: o áudio NÃO é transmitido. O host envia os arquivos uma vez (com cache) e,
a cada clique, só circula uma mensagem minúscula ("tocar o som X"). Cada PC toca o seu arquivo.
"""
import asyncio
import base64
import json
import logging
import math
import random
import time
from collections import deque
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

import protocol as P

log = logging.getLogger("net")
NET_ERRORS = (OSError, ConnectionClosed, asyncio.TimeoutError, InvalidHandshake, InvalidURI)


def dumps(o):
    return json.dumps(o, separators=(",", ":"))


def _loads(raw):
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def connect_budget(url: str) -> int:
    """Quanto tempo insistir na primeira conexão. Servidores gratuitos na internet (wss://)
    'dormem' quando ficam sem uso e levam cerca de 1 minuto para acordar; na rede local
    (ws://) não faz sentido esperar tanto."""
    return 75 if url.lower().startswith("wss://") else 12


def _open(url):
    return connect(url, max_size=P.HOST_MAX_MSG, ping_interval=20, ping_timeout=20,
                   compression=None, open_timeout=20)


async def test_relay(url, timeout=20):
    """Tenta falar com o relé. Retorna (True, milissegundos) ou (False, motivo)."""
    t0 = time.monotonic()
    try:
        async with _open(url) as ws:
            await ws.send(dumps({"t": "ping"}))
            msg = _loads(await asyncio.wait_for(ws.recv(), timeout))
            if msg and msg.get("t") == "pong":
                return True, int((time.monotonic() - t0) * 1000)
            return False, "O endereço respondeu, mas não parece ser um relé do KioBot EX."
    except NET_ERRORS:
        return False, "Não consegui conectar. Confira o endereço e a sua internet."


# ======================================================================
#  Cache de arquivos recebidos (lado do convidado)
# ======================================================================
class Cache:
    def __init__(self, root, image_ok=None):
        self.root = Path(root)
        self.image_ok = image_ok  # função opcional que valida a imagem de verdade (Qt)
        (self.root / "snd").mkdir(parents=True, exist_ok=True)
        (self.root / "img").mkdir(parents=True, exist_ok=True)

    def path(self, kind, fid):
        if not P.valid_id(fid):
            return None
        for ext in (P.AUDIO_EXTS if kind == "snd" else P.IMAGE_EXTS):
            p = self.root / kind / f"{fid}{ext}"
            if p.exists():
                return p
        return None

    def put(self, kind, fid, data: bytes):
        """Valida pelo conteúdo e grava. Levanta ValueError se algo estiver errado."""
        if not P.valid_id(fid):
            raise ValueError("id inválido")
        if kind == "snd":
            ext = P.detect_audio(data)
            if not ext or len(data) > P.MAX_SOUND_BYTES:
                raise ValueError("áudio inválido")
        elif kind == "img":
            ext = P.detect_image(data)
            if not ext or len(data) > P.MAX_IMAGE_BYTES:
                raise ValueError("imagem inválida")
            if self.image_ok and not self.image_ok(data):
                raise ValueError("imagem corrompida")
        else:
            raise ValueError("tipo inválido")
        dest = self.root / kind / f"{fid}{ext}"
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest

    def prune(self, max_bytes=300 * 1024 * 1024):
        files = [p for d in ("snd", "img") for p in (self.root / d).glob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        for p in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= max_bytes:
                break
            total -= p.stat().st_size
            p.unlink(missing_ok=True)


# ======================================================================
#  Sanitização do que chega pela rede (o convidado nunca confia no host)
# ======================================================================
def sanitize_board(raw):
    out = []
    if not isinstance(raw, list):
        return out
    for e in raw[:P.MAX_SOUNDS]:
        if not isinstance(e, dict):
            continue
        sid, ext, size = e.get("sid"), e.get("ext"), e.get("size")
        if not P.valid_id(sid) or ext not in P.AUDIO_EXTS:
            continue
        if not isinstance(size, int) or isinstance(size, bool) or not (0 < size <= P.MAX_SOUND_BYTES):
            continue
        iid, isize = e.get("iid"), e.get("isize")
        if not (P.valid_id(iid) and isinstance(isize, int) and not isinstance(isize, bool)
                and 0 < isize <= P.MAX_IMAGE_BYTES):
            iid, isize = None, 0
        out.append({
            "sid": sid, "ext": ext, "size": size,
            "name": P.clean_text(e.get("name"), P.MAX_NAME_LEN) or "Som",
            "color": e["color"] if P.valid_color(e.get("color")) else "#5865F2",
            "iid": iid, "isize": isize,
        })
    return out


def sanitize_users(raw):
    out = []
    if isinstance(raw, list):
        for u in raw[:P.MAX_GUESTS + 2]:
            if isinstance(u, dict) and isinstance(u.get("id"), str):
                out.append({"id": P.clean_text(u["id"], 12), "nick": P.clean_nick(u.get("nick")),
                            "host": bool(u.get("host"))})
    return out


def _num(v, lo, hi, default=0):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return default
    return max(lo, min(hi, v))


def sanitize_timers(raw):
    out = []
    if isinstance(raw, list):
        for t in raw[:P.MAX_TIMERS]:
            if not isinstance(t, dict) or t.get("kind") not in ("once", "repeat"):
                continue
            out.append({
                "id": P.clean_text(t.get("id"), 12),
                "kind": t["kind"],
                "remaining": _num(t.get("remaining"), 0, 86400),
                "interval": _num(t.get("interval"), 0, 86400),
                "left": int(_num(t.get("left"), 0, 10)),
                "total": int(_num(t.get("total"), 0, 10)),
                "by": P.clean_text(t.get("by"), P.MAX_NICK_LEN),
                "owner": P.clean_text(t.get("owner"), 12),
                "sid": t["sid"] if P.valid_id(t.get("sid")) else None,
            })
    return out


# ======================================================================
#  HOST
# ======================================================================
class HostSession:
    def __init__(self, url, nick, emit):
        self.url = url
        self.nick = P.clean_nick(nick)
        self.emit = emit
        self.code = None
        self.token = None
        self.manifest = []        # lista de entradas do soundboard (ver sanitize_board)
        self.paths = {}           # (kind, id) -> Path
        self.peers = {}           # peer id -> apelido
        self.timers = {}
        self._tasks = {}
        self._bg = set()
        self._ws = None
        self._stop = False
        self._next_tid = 1
        self.connect_budget = connect_budget(url)
        self._play_hist = {}
        self._global_plays = deque()
        self._need_hist = {}
        self._sending = {}

    # ---------- infraestrutura ----------
    def _fire(self, coro):
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _send(self, obj):
        ws = self._ws
        if ws is None:
            return
        try:
            await ws.send(dumps(obj))
        except ConnectionClosed:
            pass

    async def _broadcast(self, m):
        await self._send({"t": "all", "m": m})

    async def _to(self, pid, m):
        await self._send({"t": "to", "id": pid, "m": m})

    def _users(self):
        return [{"id": "host", "nick": self.nick, "host": True}] + \
               [{"id": pid, "nick": n, "host": False} for pid, n in self.peers.items()]

    def _timers_snapshot(self):
        now = time.monotonic()
        return [{"id": t["id"], "kind": t["kind"], "remaining": max(0.0, t["ends"] - now),
                 "interval": t["interval"], "left": t["left"], "total": t["total"],
                 "by": t["by"], "owner": t["owner"], "sid": t["sid"]} for t in self.timers.values()]

    def _publish_users(self):
        users = self._users()
        self.emit("users", {"list": users})
        self._fire(self._broadcast({"k": "users", "list": users}))

    def _publish_timers(self):
        snap = self._timers_snapshot()
        self.emit("timers", {"list": snap})
        self._fire(self._broadcast({"k": "timers", "list": snap}))

    def _manifest_msg(self):
        return {"k": "manifest", "host": self.nick, "board": self.manifest,
                "users": self._users(), "timers": self._timers_snapshot()}

    async def _broadcast_manifest(self):
        await self._broadcast(self._manifest_msg())

    # ---------- ciclo de vida ----------
    async def run(self):
        delay, lost_since = 1, None
        started = time.monotonic()
        while not self._stop:
            try:
                async with _open(self.url) as ws:
                    self._ws = ws
                    hello = {"t": "host"}
                    if self.code and self.token:
                        hello.update(code=self.code, token=self.token)
                    await ws.send(dumps(hello))
                    msg = _loads(await asyncio.wait_for(ws.recv(), 10))
                    if not msg or msg.get("t") != "hosted":
                        text = (msg or {}).get("msg") or "Não foi possível criar a sala."
                        self.emit("fatal", {"text": text})
                        return
                    self.code, self.token = msg["code"], msg["token"]
                    delay, lost_since = 1, None
                    if msg.get("resumed"):
                        self.peers = {p["id"]: P.clean_nick(p["nick"]) for p in msg.get("peers", [])}
                        self.emit("status", {"text": "Conexão restabelecida.", "level": "ok"})
                        self._publish_users()
                        await self._broadcast_manifest()
                    else:
                        self.emit("hosted", {"code": self.code})
                        self._publish_users()
                    async for raw in ws:
                        await self._on_relay(raw)
            except asyncio.CancelledError:
                raise
            except NET_ERRORS as ex:
                log.info("host: conexão perdida (%s)", ex)
            finally:
                self._ws = None
            if self._stop:
                break
            if self.code is None:
                if time.monotonic() - started > self.connect_budget:
                    self.emit("fatal", {"text": "Não consegui conectar ao servidor. Verifique o endereço e a internet."})
                    return
                self.emit("status", {"text": "Conectando ao servidor (na primeira vez pode levar até 1 minuto)...",
                                     "level": "warn"})
                await asyncio.sleep(2)
                continue
            lost_since = lost_since or time.monotonic()
            if time.monotonic() - lost_since > 80:
                self.emit("fatal", {"text": "A conexão com o servidor caiu e não voltou. Crie a sala novamente."})
                return
            self.emit("status", {"text": "Reconectando ao servidor...", "level": "warn"})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)

    async def close(self):
        self._stop = True
        for t in list(self._tasks.values()):
            t.cancel()
        self._tasks.clear()
        self.timers.clear()
        try:
            await self._send({"t": "close"})
            if self._ws:
                await self._ws.close()
        except Exception:
            pass

    # ---------- mensagens do relé ----------
    async def _on_relay(self, raw):
        msg = _loads(raw)
        if not msg:
            return
        t = msg.get("t")
        if t == "peer_join":
            pid = str(msg.get("id"))
            nick = P.clean_nick(msg.get("nick"))
            taken = {self.nick, *self.peers.values()}
            base, n = nick, 2
            while nick in taken:
                nick = f"{base[:P.MAX_NICK_LEN - 4]} ({n})"
                n += 1
            self.peers[pid] = nick
            self._publish_users()
            await self._to(pid, self._manifest_msg())
            self.emit("notice", {"text": f"{nick} entrou na sala."})
        elif t == "peer_leave":
            pid = str(msg.get("id"))
            nick = self.peers.pop(pid, None)
            self._sending.pop(pid, None)
            self._play_hist.pop(pid, None)
            self._need_hist.pop(pid, None)
            if nick:
                self._publish_users()
                self.emit("notice", {"text": f"{nick} saiu da sala."})
        elif t == "from":
            await self._on_guest(str(msg.get("id")), msg.get("m"))

    async def _on_guest(self, pid, m):
        if pid not in self.peers or not isinstance(m, dict):
            return
        k = m.get("k")
        if k == "need":
            self._on_need(pid, m)
        elif k == "play":
            if self._allow_play(pid) and isinstance(m.get("sid"), str):
                await self.play(m["sid"], self.peers[pid])
        elif k == "timer_add":
            ok, text = self.add_timer(pid, self.peers[pid], m.get("mode"), m.get("seconds"),
                                      m.get("times"), m.get("sid"))
            if not ok:
                await self._to(pid, {"k": "notice", "text": text})
        elif k == "timer_cancel":
            t = self.timers.get(m.get("id"))
            if t and t["owner"] == pid:
                self.cancel_timer(t["id"])

    def _allow_play(self, pid):
        now = time.monotonic()
        h = self._play_hist.setdefault(pid, deque())
        while h and now - h[0] > 1:
            h.popleft()
        g = self._global_plays
        while g and now - g[0] > 1:
            g.popleft()
        if len(h) >= 4 or len(g) >= 12:
            return False
        h.append(now)
        g.append(now)
        return True

    # ---------- soundboard ----------
    def update_board(self, manifest, paths):
        """Chamado quando o host edita o soundboard."""
        self.manifest, self.paths = manifest, paths
        self._fire(self._broadcast_manifest())

    async def play(self, sid, by):
        if not any(e["sid"] == sid for e in self.manifest):
            return
        self.emit("play", {"sid": sid, "by": by})
        await self._broadcast({"k": "play", "sid": sid, "by": by})

    # ---------- envio de arquivos ----------
    def _on_need(self, pid, m):
        now = time.monotonic()
        h = self._need_hist.setdefault(pid, deque())
        while h and now - h[0] > 10:
            h.popleft()
        if len(h) >= 6:
            return
        h.append(now)
        items = []
        for kind in ("snd", "img"):
            ids = m.get(kind)
            if isinstance(ids, list):
                items += [(kind, i) for i in ids[:P.MAX_SOUNDS] if isinstance(i, str) and (kind, i) in self.paths]
        busy = self._sending.setdefault(pid, set())
        items = [it for it in items if it not in busy]
        busy.update(items)
        if items:
            self._fire(self._send_files(pid, items))

    async def _send_files(self, pid, items):
        for kind, fid in items:
            try:
                if pid not in self.peers:
                    return
                limit = P.MAX_SOUND_BYTES if kind == "snd" else P.MAX_IMAGE_BYTES
                data = self.paths[(kind, fid)].read_bytes()
                if not data or len(data) > limit:
                    continue
                total = math.ceil(len(data) / P.CHUNK_BYTES)
                for seq in range(total):
                    if pid not in self.peers:
                        return
                    chunk = data[seq * P.CHUNK_BYTES:(seq + 1) * P.CHUNK_BYTES]
                    await self._to(pid, {"k": "file", "kind": kind, "id": fid, "seq": seq,
                                         "total": total, "b64": base64.b64encode(chunk).decode()})
            except OSError as ex:
                log.warning("não consegui ler %s: %s", fid, ex)
            finally:
                self._sending.get(pid, set()).discard((kind, fid))

    # ---------- cronômetros (rodam no host) ----------
    def add_timer(self, owner, by, mode, seconds, times=None, sid=None):
        if mode not in ("once", "repeat"):
            return False, "Tipo de cronômetro inválido."
        if len(self.timers) >= P.MAX_TIMERS:
            return False, f"Limite de {P.MAX_TIMERS} cronômetros ativos. Cancele um para criar outro."
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return False, "Tempo inválido."
        seconds = int(seconds)
        if mode == "once":
            if not (1 <= seconds <= 86400):
                return False, "O tempo precisa ficar entre 1 segundo e 24 horas."
            times = 1
        else:
            if not (60 <= seconds <= 3600):
                return False, "O intervalo precisa ficar entre 1 e 60 minutos."
            if isinstance(times, bool) or not isinstance(times, int) or not (1 <= times <= 10):
                return False, "O número de vezes precisa ficar entre 1 e 10."
        if sid is not None and not any(e["sid"] == sid for e in self.manifest):
            sid = None
        tid = f"t{self._next_tid}"
        self._next_tid += 1
        self.timers[tid] = {"id": tid, "kind": mode, "interval": seconds, "left": times, "total": times,
                            "by": P.clean_nick(by), "owner": owner, "sid": sid,
                            "ends": time.monotonic() + seconds}
        self._tasks[tid] = asyncio.ensure_future(self._run_timer(tid))
        self._publish_timers()
        return True, ""

    async def _run_timer(self, tid):
        t = self.timers[tid]
        while True:
            await asyncio.sleep(max(0.0, t["ends"] - time.monotonic()))
            sid = t["sid"] or (random.choice(self.manifest)["sid"] if self.manifest else None)
            if sid:
                await self.play(sid, f"cronômetro de {t['by']}")
            t["left"] -= 1
            if t["left"] <= 0:
                break
            t["ends"] = time.monotonic() + t["interval"]
            self._publish_timers()
        self.timers.pop(tid, None)
        self._tasks.pop(tid, None)
        self._publish_timers()

    def cancel_timer(self, tid):
        task = self._tasks.pop(tid, None)
        if task:
            task.cancel()
        if self.timers.pop(tid, None) is not None:
            self._publish_timers()

    def cancel_all_timers(self):
        for tid in list(self.timers):
            self.cancel_timer(tid)

    async def kick(self, pid):
        await self._send({"t": "kick", "id": pid})


# ======================================================================
#  CONVIDADO
# ======================================================================
class GuestSession:
    def __init__(self, url, code, nick, cache, emit):
        self.url, self.code, self.nick = url, code, P.clean_nick(nick)
        self.cache, self.emit = cache, emit
        self.my_id = None
        self.board = []
        self._ws = None
        self._stop = False
        self.connect_budget = connect_budget(url)
        self._expected = {}   # (kind, id) -> tamanho esperado
        self._partial = {}
        self._requested = set()

    async def send_app(self, m):
        ws = self._ws
        if ws is not None:
            try:
                await ws.send(dumps({"t": "m", "m": m}))
            except ConnectionClosed:
                pass

    async def close(self):
        self._stop = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def run(self):
        connected_once, lost_since, delay = False, None, 2
        started = time.monotonic()
        while not self._stop:
            try:
                async with _open(self.url) as ws:
                    self._ws = ws
                    await ws.send(dumps({"t": "join", "code": self.code, "nick": self.nick}))
                    async for raw in ws:
                        res = self._on_relay(raw)
                        if res == "joined":
                            connected_once, lost_since, delay = True, None, 2
                        elif res == "stop":
                            return
            except asyncio.CancelledError:
                raise
            except NET_ERRORS as ex:
                log.info("guest: conexão perdida (%s)", ex)
            finally:
                self._ws = None
            if self._stop:
                return
            if not connected_once:
                if time.monotonic() - started > self.connect_budget:
                    self.emit("fatal", {"text": "Não consegui conectar ao servidor. Verifique o convite e a internet."})
                    return
                self.emit("status", {"text": "Conectando ao servidor (na primeira vez pode levar até 1 minuto)...",
                                     "level": "warn"})
                await asyncio.sleep(2)
                continue
            lost_since = lost_since or time.monotonic()
            if time.monotonic() - lost_since > 600:
                self.emit("fatal", {"text": "A conexão caiu e não voltou. Entre na sala novamente."})
                return
            self.emit("status", {"text": "Reconectando à sala...", "level": "warn"})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8)

    # ---------- mensagens ----------
    def _on_relay(self, raw):
        msg = _loads(raw)
        if not msg:
            return None
        t = msg.get("t")
        if t == "joined":
            self.my_id = str(msg.get("id"))
            self._requested, self._partial = set(), {}
            self.emit("joined", {"id": self.my_id})
            if msg.get("host_away"):
                self.emit("status", {"text": "O host perdeu a conexão. Aguardando...", "level": "warn"})
            return "joined"
        if t == "error":
            text = str(msg.get("msg") or "Erro ao entrar na sala.")[:200]
            self.emit("fatal", {"text": text})
            return "stop"
        if t == "closed":
            self.emit("closed", {"text": str(msg.get("msg") or "A sala foi encerrada.")[:200]})
            return "stop"
        if t == "host_away":
            self.emit("status", {"text": "O host perdeu a conexão. Aguardando...", "level": "warn"})
        elif t == "host_back":
            self.emit("status", {"text": "Conectado.", "level": "ok"})
        elif t == "m" and isinstance(msg.get("m"), dict):
            self._on_app(msg["m"])
        return None

    def _on_app(self, m):
        k = m.get("k")
        if k == "manifest":
            self._on_manifest(m)
        elif k == "file":
            self._on_file(m)
        elif k == "play":
            sid = m.get("sid")
            if any(e["sid"] == sid for e in self.board) and self.cache.path("snd", sid):
                self.emit("play", {"sid": sid, "by": P.clean_text(m.get("by"), 40)})
        elif k == "users":
            self.emit("users", {"list": sanitize_users(m.get("list"))})
        elif k == "timers":
            self.emit("timers", {"list": sanitize_timers(m.get("timers") or m.get("list"))})
        elif k == "notice":
            self.emit("notice", {"text": P.clean_text(m.get("text"), 200)})

    def _on_manifest(self, m):
        self.board = sanitize_board(m.get("board"))
        self.emit("host_nick", {"nick": P.clean_nick(m.get("host"))})
        self.emit("users", {"list": sanitize_users(m.get("users"))})
        self.emit("timers", {"list": sanitize_timers(m.get("timers"))})
        expected = {}
        for e in self.board:
            if not self.cache.path("snd", e["sid"]):
                expected[("snd", e["sid"])] = e["size"]
            if e["iid"] and not self.cache.path("img", e["iid"]):
                expected[("img", e["iid"])] = e["isize"]
        self._expected = expected
        self._partial = {k: v for k, v in self._partial.items() if k in expected}
        fresh = [k for k in expected if k not in self._requested]
        self._requested.update(fresh)
        self.emit("board", {"board": self.board})
        self._progress()
        if fresh:
            asyncio.ensure_future(self.send_app({
                "k": "need",
                "snd": sorted({i for kd, i in fresh if kd == "snd"}),
                "img": sorted({i for kd, i in fresh if kd == "img"})}))

    def _progress(self):
        total = len({e["sid"] for e in self.board})
        done = sum(1 for s in {e["sid"] for e in self.board} if self.cache.path("snd", s))
        self.emit("progress", {"done": done, "total": total})

    def _on_file(self, m):
        kind, fid = m.get("kind"), m.get("id")
        key = (kind, fid)
        size = self._expected.get(key)
        seq, total, b64 = m.get("seq"), m.get("total"), m.get("b64")
        if size is None or not isinstance(seq, int) or not isinstance(total, int) or not isinstance(b64, str):
            return
        if isinstance(seq, bool) or not (1 <= total <= 64 and 0 <= seq < total):
            return
        if len(b64) > P.CHUNK_BYTES * 4 // 3 + 16:
            return
        try:
            chunk = base64.b64decode(b64, validate=True)
        except ValueError:
            return
        st = self._partial.get(key)
        if st is None or st["total"] != total:
            st = self._partial[key] = {"total": total, "parts": {}}
        st["parts"][seq] = chunk
        if sum(len(c) for c in st["parts"].values()) > size:
            self._partial.pop(key, None)
            return
        if len(st["parts"]) == total:
            data = b"".join(st["parts"][i] for i in range(total))
            self._partial.pop(key, None)
            if len(data) != size or P.content_id(data) != fid:
                self.emit("notice", {"text": "Um arquivo chegou corrompido e foi ignorado."})
                self._requested.discard(key)
                return
            try:
                self.cache.put(kind, fid, data)
            except ValueError as ex:
                self.emit("notice", {"text": f"Arquivo recusado: {ex}."})
                self._requested.discard(key)
                return
            self._expected.pop(key, None)
            self.emit("file_ready", {"kind": kind, "id": fid})
            self._progress()
