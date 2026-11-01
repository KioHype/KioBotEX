"""Teste de ponta a ponta da rede (sem Qt). Rodar:  python tests/test_flow.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import protocol as P
import relay
from net import Cache, GuestSession, HostSession, sanitize_board, test_relay

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FALHOU ") + name)


class Collector:
    def __init__(self):
        self.events = []

    def __call__(self, name, data):
        self.events.append((name, data))

    def of(self, name):
        return [d for n, d in self.events if n == name]

    async def wait(self, name, pred=lambda d: True, timeout=8):
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            for d in self.of(name):
                if pred(d):
                    return d
            await asyncio.sleep(0.05)
        return None


def make_files(folder: Path):
    mp3 = lambda n: b"ID3" + os.urandom(n)
    items = {"a.mp3": mp3(100_000), "b.mp3": mp3(500_000), "c.wav": b"RIFF\0\0\0\0WAVE" + os.urandom(5000)}
    manifest, paths = [], {}
    for i, (fn, data) in enumerate(items.items()):
        (folder / fn).write_bytes(data)
        sid = P.content_id(data)
        ext = Path(fn).suffix
        manifest.append({"sid": sid, "ext": ext, "size": len(data), "name": fn, "color": "#5865F2",
                         "iid": None, "isize": 0})
        paths[("snd", sid)] = folder / fn
    return manifest, paths


async def main():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "host").mkdir()
    manifest, paths = make_files(tmp / "host")
    server, srv = await relay.start_server("127.0.0.1", 0, grace_seconds=30)
    port = server.sockets[0].getsockname()[1]
    url = f"ws://127.0.0.1:{port}"

    # ---- host cria sala
    hev = Collector()
    host = HostSession(url, "Kio", hev)
    host.manifest, host.paths = manifest, paths
    htask = asyncio.create_task(host.run())
    h = await hev.wait("hosted")
    check("host recebe código da sala", bool(h) and P.normalize_code(h["code"]) == h["code"])
    code = h["code"]

    # ---- convidados entram (mesmo apelido)
    g1ev, g2ev = Collector(), Collector()
    c1, c2 = Cache(tmp / "c1"), Cache(tmp / "c2")
    g1 = GuestSession(url, code, "Ana", c1, g1ev)
    g2 = GuestSession(url, code, "Kio", c2, g2ev)
    t1, t2 = asyncio.create_task(g1.run()), asyncio.create_task(g2.run())
    await g1ev.wait("joined"); await g2ev.wait("joined")
    check("convidados entram", bool(g1.my_id and g2.my_id))
    check("apelido repetido recebe sufixo", set(host.peers.values()) == {"Ana", "Kio (2)"})

    # ---- transferência de arquivos
    ok1 = await g1ev.wait("progress", lambda d: d["done"] == d["total"] == 3)
    ok2 = await g2ev.wait("progress", lambda d: d["done"] == d["total"] == 3)
    check("convidados baixam todos os sons", bool(ok1 and ok2))
    same = all(c1.path("snd", e["sid"]) and c1.path("snd", e["sid"]).read_bytes() == paths[("snd", e["sid"])].read_bytes()
               for e in manifest)
    check("arquivos idênticos ao original", same)
    users = (await g1ev.wait("users", lambda d: len(d["list"]) == 3))
    check("lista de usuários chega", bool(users))

    # ---- host edita o soundboard com a sala aberta
    extra = b"ID3" + os.urandom(300_000)
    (tmp / "host" / "d.mp3").write_bytes(extra)
    esid = P.content_id(extra)
    m2 = manifest + [{"sid": esid, "ext": ".mp3", "size": len(extra), "name": "novo", "color": "#8b5cf6",
                      "iid": None, "isize": 0}]
    p2 = dict(paths); p2[("snd", esid)] = tmp / "host" / "d.mp3"
    host.update_board(m2, p2)
    got = await g1ev.wait("progress", lambda d: d["total"] == 4 and d["done"] == 4)
    check("som novo adicionado pelo host chega aos convidados", bool(got) and c1.path("snd", esid) is not None)
    host.update_board(manifest, paths)
    await g1ev.wait("board", lambda d: len(d["board"]) == 3)

    # ---- clique do convidado toca para todos
    sid = manifest[0]["sid"]
    await g1.send_app({"k": "play", "sid": sid})
    p_host = await hev.wait("play", lambda d: d["sid"] == sid)
    p_g2 = await g2ev.wait("play", lambda d: d["sid"] == sid)
    p_g1 = await g1ev.wait("play", lambda d: d["sid"] == sid)
    check("play chega ao host e aos convidados", bool(p_host and p_g1 and p_g2))
    check("play informa quem tocou", p_host and p_host["by"] == "Ana")

    # ---- limite de cliques
    before = len(g2ev.of("play"))
    for _ in range(25):
        await g1.send_app({"k": "play", "sid": sid})
    await asyncio.sleep(1.0)
    got = len(g2ev.of("play")) - before
    check(f"spam de cliques é limitado ({got} de 25)", got <= 12)

    # ---- id inexistente é ignorado
    before = len(hev.of("play"))
    await g1.send_app({"k": "play", "sid": "0" * 32})
    await asyncio.sleep(0.4)
    check("som inexistente é ignorado", len(hev.of("play")) == before)

    # ---- cronômetros
    ok, _ = host.add_timer("host", "Kio", "once", 1)
    pl = await g1ev.wait("play", lambda d: d["by"].startswith("cronômetro de Kio"), timeout=5)
    check("cronômetro de 1s toca para todos", bool(pl))
    check("cronômetro 'uma vez' some depois de tocar", not host.timers)

    ok, msg = host.add_timer("host", "Kio", "repeat", 10, 2)
    check("repetir com intervalo < 1 min é recusado", not ok)
    ok, msg = host.add_timer("host", "Kio", "repeat", 60, 11)
    check("repetir mais de 10 vezes é recusado", not ok)
    ok, _ = host.add_timer("host", "Kio", "repeat", 60, 2, sid)
    check("repetir válido é aceito", ok)
    for _ in range(4):
        host.add_timer("host", "Kio", "once", 3600)
    ok, msg = host.add_timer("host", "Kio", "once", 3600)
    check("limite de 5 cronômetros", (not ok) and len(host.timers) == 5)
    snap = await g1ev.wait("timers", lambda d: len(d["list"]) == 5)
    check("convidado vê a lista de cronômetros", bool(snap))
    host.cancel_all_timers()
    check("cancelar todos limpa a fila", not host.timers)

    await g1.send_app({"k": "timer_add", "mode": "once", "seconds": 3000, "sid": None})
    await asyncio.sleep(0.5)
    check("convidado cria cronômetro", len(host.timers) == 1)
    tid = next(iter(host.timers))
    await g2.send_app({"k": "timer_cancel", "id": tid})
    await asyncio.sleep(0.4)
    check("outro convidado NÃO cancela cronômetro alheio", len(host.timers) == 1)
    await g1.send_app({"k": "timer_cancel", "id": tid})
    await asyncio.sleep(0.4)
    check("dono cancela o próprio cronômetro", not host.timers)
    await g1.send_app({"k": "timer_add", "mode": "once", "seconds": -5})
    await g1.send_app({"k": "timer_add", "mode": "once", "seconds": "abc"})
    await g1.send_app({"k": "timer_add", "mode": "xyz", "seconds": 5})
    await asyncio.sleep(0.4)
    check("cronômetros inválidos são recusados", not host.timers)

    # ---- mensagem gigante de convidado é descartada
    await g1._ws.send('{"t":"m","m":{"k":"play","sid":"' + "a" * 6000 + '"}}')
    await asyncio.sleep(0.3)
    check("mensagem acima do limite não derruba nada", g1._ws is not None)

    # ---- relé: código errado
    bad = Collector()
    gb = GuestSession(url, "AAAAAAAA", "X", Cache(tmp / "cb"), bad)
    await gb.run()
    f = bad.of("fatal")
    check("código inexistente retorna erro", bool(f) and "não encontrada" in f[0]["text"])

    # ---- host cai e volta (reconexão)
    await host._ws.close()
    away = await g1ev.wait("status", lambda d: "host" in d["text"].lower() and "perdeu" in d["text"].lower(), timeout=5)
    back = await g1ev.wait("status", lambda d: d["text"] == "Conectado.", timeout=10)
    check("convidados são avisados quando o host cai", bool(away))
    check("host reconecta e a sala continua", bool(back) and host._ws is not None)
    n = len(g2ev.of("play"))
    await g2.send_app({"k": "play", "sid": sid})
    await asyncio.sleep(0.6)
    check("depois de reconectar, play funciona", len(g2ev.of("play")) > n)

    # ---- expulsar
    pid = next(p for p, n in host.peers.items() if n == "Ana")
    await host.kick(pid)
    await asyncio.sleep(0.6)
    check("expulsão remove o convidado", "Ana" not in host.peers.values())
    kicked = await g1ev.wait("closed", lambda d: "removido" in d["text"], timeout=3)
    check("convidado expulso é avisado e não volta sozinho", bool(kicked) and t1.done())
    rej = Collector()
    await GuestSession(url, code, "Ana", Cache(tmp / "cr"), rej).run()
    check("convidado expulso não consegue entrar de novo", any("removido" in d["text"] for d in rej.of("fatal")))

    # ---- encerrar sala
    await host.close()
    closed = await g2ev.wait("closed", timeout=5)
    check("convidados recebem aviso de sala encerrada", bool(closed))
    await asyncio.gather(htask, t1, t2, return_exceptions=True)

    # ---- botão "Testar conexão"
    ok_t, ms = await test_relay(url)
    check("testar conexão: relé responde", ok_t and isinstance(ms, int))
    bad_t, why = await test_relay("ws://127.0.0.1:1")
    check("testar conexão: endereço morto é reportado", (not bad_t) and "conectar" in why)

    # ---- servidor inalcançável: insiste por um tempo e depois avisa
    unreach = Collector()
    hh = HostSession("ws://127.0.0.1:1", "X", unreach)
    hh.connect_budget = 3
    t0 = asyncio.get_event_loop().time()
    await hh.run()
    dt = asyncio.get_event_loop().time() - t0
    check("servidor inalcançável: avisa e depois desiste", bool(unreach.of("fatal")) and bool(unreach.of("status")) and 2.5 < dt < 12)
    check("orçamento de conexão: wss espera mais que ws", P and __import__("net").connect_budget("wss://x") > __import__("net").connect_budget("ws://x"))

    # ---- sanitização / arquivo corrompido (convidado nunca confia no host)
    evil = [{"sid": "zz", "ext": ".mp3", "size": 10, "name": "x"},
            {"sid": "a" * 32, "ext": ".exe", "size": 10, "name": "x"},
            {"sid": "b" * 32, "ext": ".mp3", "size": 10**9, "name": "x"},
            {"sid": "c" * 32, "ext": ".mp3", "size": 10, "name": "n\n" + "x" * 500, "color": "javascript:1"}]
    clean = sanitize_board(evil)
    check("manifesto malicioso é filtrado", len(clean) == 1 and len(clean[0]["name"]) <= P.MAX_NAME_LEN
          and clean[0]["color"] == "#5865F2" and "\n" not in clean[0]["name"])

    ev = Collector()
    gs = GuestSession("ws://x", "AAAAAAAA", "t", Cache(tmp / "cx"), ev)
    data = b"ID3" + b"1" * 50
    gs._on_manifest({"board": [{"sid": P.content_id(data), "ext": ".mp3", "size": len(data), "name": "ok"}]})
    gs._on_file({"kind": "snd", "id": P.content_id(data), "seq": 0, "total": 1,
                 "b64": __import__("base64").b64encode(b"ID3" + b"2" * 50).decode()})
    check("arquivo com hash errado é recusado", gs.cache.path("snd", P.content_id(data)) is None)
    gs._on_file({"kind": "snd", "id": "d" * 32, "seq": 0, "total": 1, "b64": "AAAA"})
    check("arquivo não solicitado é ignorado", not gs._partial and gs.cache.path("snd", "d" * 32) is None)
    exe = b"MZ" + b"0" * 50
    gs._on_manifest({"board": [{"sid": P.content_id(exe), "ext": ".mp3", "size": len(exe), "name": "ok"}]})
    gs._on_file({"kind": "snd", "id": P.content_id(exe), "seq": 0, "total": 1,
                 "b64": __import__("base64").b64encode(exe).decode()})
    check("arquivo que não é áudio é recusado", gs.cache.path("snd", P.content_id(exe)) is None)
    gs._on_manifest({"board": [{"sid": P.content_id(data), "ext": ".mp3", "size": len(data), "name": "ok"}]})
    gs._on_file({"kind": "snd", "id": P.content_id(data), "seq": 0, "total": 1,
                 "b64": __import__("base64").b64encode(data).decode()})
    check("arquivo válido é aceito", gs.cache.path("snd", P.content_id(data)) is not None)

    # ---- convites
    check("convite: só código", P.parse_invite("abcd-2345") == (None, "ABCD2345"))
    check("convite: rede local", P.parse_invite("192.168.0.10:8765/abcd2345") == ("ws://192.168.0.10:8765", "ABCD2345"))
    check("convite com endereço do relé (túnel)", P.parse_invite("wss://abc-def-ghi.trycloudflare.com/ABCD-2345") == ("wss://abc-def-ghi.trycloudflare.com", "ABCD2345"))
    check("endereço https vira wss", P.normalize_relay_url("https://abc.trycloudflare.com/") == "wss://abc.trycloudflare.com"
          and P.normalize_relay_url("abc.com") == "wss://abc.com" and P.normalize_relay_url("") == "")
    check("convite inválido", P.parse_invite("lixo")[1] == "" and P.parse_invite("http://x/ABCD2345") == (None, ""))

    server.close()
    await server.wait_closed()
    print(f"\n{len(PASS)} ok, {len(FAIL)} falharam")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
