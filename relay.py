"""Relé do KioBot EX.

Não toca nem guarda áudio. Só repassa mensagens curtas entre o host de uma sala
e os convidados, usando o código da sala. Quem manda na sala é sempre o host.

Rodar:  python relay.py            (porta 8765, ou a variável PORT; HOST=127.0.0.1 para aceitar só conexões locais)
Atrás de proxy/hospedagem com TLS: defina TRUST_PROXY=1 para ler o IP real.
"""
import asyncio
import json
import logging
import os
import secrets
import time
from http import HTTPStatus

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import protocol as P

log = logging.getLogger("relay")


class Bucket:
    """Limitador de mensagens por conexão (token bucket)."""

    def __init__(self, rate, burst):
        self.rate, self.burst = rate, burst
        self.tokens, self.t = burst, time.monotonic()

    def allow(self):
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


def _parse(raw):
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def _dumps(o):
    return json.dumps(o, separators=(",", ":"))


class Room:
    def __init__(self, code, token, host):
        self.code, self.token, self.host = code, token, host
        self.guests = {}          # id -> (ws, nick)
        self.ips = {}             # id -> ip
        self.banned = set()       # IPs expulsos desta sala
        self.next_id = 1
        self.grace_task = None


class RelayServer:
    def __init__(self, max_rooms=200, max_guests=P.MAX_GUESTS, grace_seconds=90, trust_proxy=False):
        self.max_rooms, self.max_guests = max_rooms, max_guests
        self.grace_seconds, self.trust_proxy = grace_seconds, trust_proxy
        self.rooms = {}
        self._fails = {}          # ip -> [timestamps]
        self._blocked = {}        # ip -> until

    # ---------- utilidades ----------
    def _ip(self, ws):
        if self.trust_proxy and ws.request:
            h = ws.request.headers
            # CF-Connecting-IP é definido pela Cloudflare e não pode ser forjado pelo cliente.
            # No X-Forwarded-For vale o ÚLTIMO valor: é o que o nosso proxy acrescentou.
            cf = h.get("CF-Connecting-IP")
            if cf:
                return cf.strip()
            xff = h.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[-1].strip()
        ra = ws.remote_address
        return ra[0] if ra else "?"

    def _is_blocked(self, ip):
        until = self._blocked.get(ip)
        if until and until > time.monotonic():
            return True
        self._blocked.pop(ip, None)
        return False

    def _register_failure(self, ip):
        now = time.monotonic()
        lst = [t for t in self._fails.get(ip, []) if now - t < 60] + [now]
        self._fails[ip] = lst
        if len(lst) >= 8:
            self._blocked[ip] = now + 120
            self._fails.pop(ip, None)

    async def _send(self, ws, obj):
        try:
            await ws.send(_dumps(obj))
        except ConnectionClosed:
            pass

    async def _send_raw(self, ws, data):
        try:
            await ws.send(data)
        except ConnectionClosed:
            pass

    async def _to_guests(self, room, obj):
        data = _dumps(obj)
        await asyncio.gather(*(self._send_raw(g, data) for g, _ in list(room.guests.values())))

    async def _close_room(self, room, reason):
        self.rooms.pop(room.code, None)
        if room.grace_task:
            room.grace_task.cancel()
        for g, _ in list(room.guests.values()):
            await self._send(g, {"t": "closed", "msg": reason})
            try:
                await g.close(1000, "sala encerrada")
            except Exception:
                pass
        room.guests.clear()

    async def _grace(self, room):
        try:
            await asyncio.sleep(self.grace_seconds)
        except asyncio.CancelledError:
            return
        if room.host is None:
            await self._close_room(room, "O host saiu da sala.")

    # ---------- entrada ----------
    async def handler(self, ws):
        ip = self._ip(ws)
        if self._is_blocked(ip):
            await ws.close(1008, "muitas tentativas")
            return
        try:
            msg = _parse(await asyncio.wait_for(ws.recv(), timeout=10))
        except (asyncio.TimeoutError, ConnectionClosed):
            return
        if not msg:
            await ws.close(1008, "mensagem inválida")
            return
        t = msg.get("t")
        if t == "ping":                      # usado pelo botão "Testar conexão"
            await self._send(ws, {"t": "pong"})
            await ws.close()
            return
        if t == "host":
            await self._run_host(ws, msg)
        elif t == "join":
            await self._run_guest(ws, msg, ip)
        else:
            await ws.close(1008, "mensagem inválida")

    # ---------- host ----------
    async def _run_host(self, ws, msg):
        code, token = msg.get("code"), msg.get("token")
        resumed = False
        if code and token:
            room = self.rooms.get(str(code))
            if not room or not secrets.compare_digest(room.token, str(token)):
                await self._send(ws, {"t": "error", "msg": "Sala não existe mais."})
                await ws.close(1008, "sala inexistente")
                return
            if room.grace_task:
                room.grace_task.cancel()
                room.grace_task = None
            old = room.host
            room.host = ws
            if old is not None and old is not ws:
                try:
                    await old.close(1000, "substituído")
                except Exception:
                    pass
            resumed = True
        else:
            if len(self.rooms) >= self.max_rooms:
                await self._send(ws, {"t": "error", "msg": "Servidor cheio. Tente mais tarde."})
                await ws.close(1013, "cheio")
                return
            code = P.new_code()
            while code in self.rooms:
                code = P.new_code()
            room = Room(code, secrets.token_urlsafe(16), ws)
            self.rooms[code] = room

        peers = [{"id": gid, "nick": nick} for gid, (_, nick) in room.guests.items()]
        await self._send(ws, {"t": "hosted", "code": room.code, "token": room.token,
                              "resumed": resumed, "peers": peers})
        if resumed:
            await self._to_guests(room, {"t": "host_back"})

        bucket = Bucket(rate=300, burst=600)
        explicit_close = False
        try:
            async for raw in ws:
                if not isinstance(raw, str) or not bucket.allow():
                    continue
                m = _parse(raw)
                if not m:
                    continue
                t = m.get("t")
                if t == "all" and isinstance(m.get("m"), dict):
                    await self._to_guests(room, {"t": "m", "m": m["m"]})
                elif t == "to" and isinstance(m.get("m"), dict):
                    g = room.guests.get(m.get("id"))
                    if g:
                        await self._send(g[0], {"t": "m", "m": m["m"]})
                elif t == "kick":
                    g = room.guests.get(m.get("id"))
                    if g:
                        ip = room.ips.get(m.get("id"))
                        if ip:
                            room.banned.add(ip)
                        await self._send(g[0], {"t": "closed", "msg": "Você foi removido da sala pelo host."})
                        try:
                            await g[0].close(4000, "removido pelo host")
                        except Exception:
                            pass
                elif t == "close":
                    explicit_close = True
                    break
        except ConnectionClosed:
            pass
        finally:
            if room.host is ws:
                room.host = None
                if explicit_close:
                    await self._close_room(room, "O host encerrou a sala.")
                else:
                    await self._to_guests(room, {"t": "host_away"})
                    room.grace_task = asyncio.create_task(self._grace(room))

    # ---------- convidado ----------
    async def _run_guest(self, ws, msg, ip):
        code = P.normalize_code(msg.get("code"))
        nick = P.clean_nick(msg.get("nick"))
        room = self.rooms.get(code) if code else None
        if room is None:
            self._register_failure(ip)
            await self._send(ws, {"t": "error", "msg": "Sala não encontrada. Confira o código."})
            await ws.close(1008, "sala não encontrada")
            return
        if ip in room.banned:
            await self._send(ws, {"t": "error", "msg": "Você foi removido desta sala pelo host."})
            await ws.close(1008, "removido")
            return
        if len(room.guests) >= self.max_guests:
            await self._send(ws, {"t": "error", "msg": "A sala está cheia."})
            await ws.close(1008, "sala cheia")
            return
        gid = f"g{room.next_id}"
        room.next_id += 1
        room.guests[gid] = (ws, nick)
        room.ips[gid] = ip
        await self._send(ws, {"t": "joined", "id": gid, "host_away": room.host is None})
        if room.host is not None:
            await self._send(room.host, {"t": "peer_join", "id": gid, "nick": nick})
        bucket = Bucket(rate=10, burst=25)
        try:
            async for raw in ws:
                if not isinstance(raw, str) or len(raw) > P.GUEST_MAX_MSG or not bucket.allow():
                    continue
                m = _parse(raw)
                if m and m.get("t") == "m" and isinstance(m.get("m"), dict) and room.host is not None:
                    await self._send(room.host, {"t": "from", "id": gid, "m": m["m"]})
        except ConnectionClosed:
            pass
        finally:
            room.guests.pop(gid, None)
            room.ips.pop(gid, None)
            if room.host is not None:
                await self._send(room.host, {"t": "peer_leave", "id": gid})


def _process_request(connection, request):
    # Responde 200 a requisições HTTP comuns (health check de hospedagens).
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return connection.respond(HTTPStatus.OK, "KioBot EX relay\n")
    return None


async def start_server(host="0.0.0.0", port=P.LAN_PORT, **kw):
    srv = RelayServer(**kw)
    server = await serve(srv.handler, host, port, max_size=P.HOST_MAX_MSG, ping_interval=20,
                         ping_timeout=20, compression=None, process_request=_process_request)
    return server, srv


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    port = int(os.environ.get("PORT", P.LAN_PORT))
    trust = os.environ.get("TRUST_PROXY", "") == "1"
    if "PORT" in os.environ and not trust:
        log.warning("Rodando com PORT definida (provável proxy). Defina TRUST_PROXY=1 para que "
                    "limites e expulsões usem o IP real de cada pessoa.")
    host = os.environ.get("HOST", "0.0.0.0")
    server, _ = await start_server(host, port, trust_proxy=trust)
    log.info("Relé do KioBot EX rodando na porta %s", port)
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
