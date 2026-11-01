"""Teste de fumaça da interface (sem tela). Rodar:  python tests/test_ui.py [pasta_de_capturas]"""
import os
import sys
import tempfile
import time
import wave
import struct
import math
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["KIOBOT_NO_AUDIO"] = "1"
tmp = Path(tempfile.mkdtemp())
os.environ["XDG_CONFIG_HOME"] = str(tmp / "cfg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from ui import MainWindow
from widgets import QSS

SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)
PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FALHOU ") + name)


app = QApplication([])
app.setStyleSheet(QSS)


def pump(ms=300, until=None, timeout=8000):
    end = time.time() + (timeout if until else ms) / 1000
    while time.time() < end:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.02)
    return until() if until else True


def shot(w, name):
    if SHOTS:
        w.grab().save(str(SHOTS / f"{name}.png"))


def tone(path, freq, secs=0.4):
    with wave.open(str(path), "w") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(22050)
        f.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / 22050)))
                               for i in range(int(22050 * secs))))


folder = tmp / "sons"
folder.mkdir()
names = ["vine_boom", "bruh", "airhorn", "sad_violin", "oof", "windows_xp", "discord_ping", "fahhh"]
for i, n in enumerate(names):
    tone(folder / f"{n}.wav", 300 + i * 70)
img = QImage(300, 200, QImage.Format.Format_ARGB32)
img.fill(QColor("#ffcc00"))
img.save(str(tmp / "foto.png"))

host, guest = MainWindow(), MainWindow()
host.resize(1180, 760); guest.resize(1180, 760)
host.show(); guest.show()
host.home.nick.setText("Kio")
shot(host, "01_home")

host.st["folder"] = str(folder)
host.on_host({"nick": "Kio", "folder": str(folder), "mode": "lan"})
ok = pump(until=lambda: host.room.invite_text != "")
check("host cria sala e mostra o convite", ok and "/" in host.room.invite_text)
check("host vê todos os botões do soundboard", len(host.room.grid.tiles) == len(names))

guest.on_join({"nick": "Ana", "invite": host.room.invite_text.replace(P := "", "")})
ok = pump(until=lambda: guest.room and guest.room.grid.tiles and all(t.ready for t in guest.room.grid.tiles))
check("convidado baixa os sons e os botões ficam ativos", ok)
check("lista de usuários mostra 2 pessoas", pump(until=lambda: "(2)" in host.room.users_title.text()))

# clique do convidado toca no host
guest.room._tile(0)
ok = pump(until=lambda: "Ana" in host.room.now.text())
check("clique do convidado aparece no host", ok)
check("o som toca localmente no host e no convidado", pump(until=lambda: len(host.audio.played) >= 1 and len(guest.audio.played) >= 1))

# edição pelo host
r = host.room
r.btn_edit.setChecked(True)
r._tile(1)
r.editor.name.setText("Bruh moment")
r.editor.flush()
r._recolor("#EC4899")
r._image(str(tmp / "foto.png"))
r._tile(2)
r._move(-1)
pump(500)
check("host renomeou o botão", r.board.entries[1]["name"] == "Bruh moment" or r.board.entries[2]["name"] == "Bruh moment")
check("imagem foi processada e salva", any(e["image"] for e in r.board.entries))
check("ordem mudou", [e["file"] for e in r.board.entries][:3] != ["airhorn.wav", "bruh.wav", "discord_ping.wav"])
ok = pump(until=lambda: any(t.entry["name"] == "Bruh moment" for t in guest.room.grid.tiles))
check("convidado vê o nome novo", ok)
ok = pump(until=lambda: any(t.entry["iid"] for t in guest.room.grid.tiles) and
          all(guest.room.image_path(t.entry["iid"]) for t in guest.room.grid.tiles if t.entry["iid"]))
check("convidado recebeu a imagem do botão", ok)
shot(host, "02_host_edicao")
r.btn_edit.setChecked(False)

# cronômetros
r.tabs.setCurrentIndex(1)
r.timers.sp_h.setValue(0); r.timers.sp_m.setValue(0); r.timers.sp_s.setValue(2)
r.timers._start()
guest.room.tabs.setCurrentIndex(1)
guest.room.timers.b_rep.click()
guest.room.timers._start()
pump(800)
check("host vê cronômetros ativos", len(r.timers.rows) == 2)
check("convidado vê cronômetros ativos", len(guest.room.timers.rows) == 2)
shot(host, "03_host_cronometro")
shot(guest, "04_convidado_cronometro")
ok = pump(until=lambda: "cronômetro" in guest.room.now.text(), timeout=6000)
check("cronômetro de 2s tocou para o convidado", ok)
guest.room.tabs.setCurrentIndex(0)
pump(300)
shot(guest, "05_convidado_soundboard")

# expulsar
pid = next(iter(r.rt.session.peers))
r.rt.kick(pid)
ok = pump(until=lambda: guest.room is None)
check("convidado expulso volta para a tela inicial", ok)
shot(guest, "06_convidado_expulso")

host.leave()
check("host encerra a sala e volta para a tela inicial", host.room is None)
# ---- sala pela internet (relé próprio); o amigo NÃO configura nada
import asyncio, relay
fut = asyncio.run_coroutine_threadsafe(relay.start_server("127.0.0.1", 18765), host.rt.loop)
srv, _ = fut.result(timeout=5)
host.st["relay_url"] = "ws://127.0.0.1:18765"
check("amigo não tem relé configurado", guest.st["relay_url"] == "")
host.on_host({"nick": "Kio", "folder": str(folder), "mode": "internet"})
ok = pump(until=lambda: host.room.invite_text != "")
check("convite da internet leva o endereço do relé", ok and host.room.invite_text == f"ws://127.0.0.1:18765/{host.room.invite_text.rsplit('/', 1)[-1]}")
guest.on_join({"nick": "Bia", "invite": host.room.invite_text})
ok = pump(until=lambda: guest.room and guest.room.grid.tiles and all(t.ready for t in guest.room.grid.tiles))
check("amigo entra pelo relé só com o convite e baixa os sons", ok)
guest.room._tile(0)
check("clique do amigo chega ao host pelo relé", pump(until=lambda: "Bia" in host.room.now.text()))
from ui import SettingsDialog
dlg = SettingsDialog(host.st, host.rt)
dlg.url.setText("http://127.0.0.1:18765")
dlg._test()
check("configurações: testar conexão mostra sucesso", pump(until=lambda: "Conectou" in dlg.test_lbl.text()))
dlg.url.setText("ws://127.0.0.1:1")
dlg._test()
check("configurações: testar conexão mostra erro", pump(until=lambda: "Não consegui" in dlg.test_lbl.text()))
dlg.close()
host.leave()
check("amigo é avisado quando o host encerra a sala", pump(until=lambda: guest.room is None))
# ---- internet automática (túnel aberto pelo próprio programa; aqui, um cloudflared de mentira)
os.environ["KIOBOT_CLOUDFLARED"] = str(Path(__file__).resolve().parent / "fake_cloudflared.py")
os.environ["FAKE_MODE"] = "ok"
host.st["relay_url"] = ""
host.on_host({"nick": "Kio", "folder": str(folder), "mode": "internet"})
ok = pump(until=lambda: host.room is not None and host.room.invite_text != "")
check("internet automática: convite usa o endereço público do túnel",
      ok and host.room.invite_text.startswith("test-fake-words-here.trycloudflare.com/"))
check("o programa abriu o túnel sozinho", host.rt.tunnel is not None and host.rt.tunnel.proc.returncode is None)
code = host.room.invite_text.rsplit("/", 1)[-1]
guest.on_join({"nick": "Cia", "invite": f"ws://127.0.0.1:{host.rt.lan_port}/{code}"})
ok = pump(until=lambda: guest.room and guest.room.grid.tiles and all(t.ready for t in guest.room.grid.tiles))
check("convidado entra pelo relé embutido e baixa os sons", ok)
proc = host.rt.tunnel.proc
host.leave()
check("ao encerrar a sala o túnel é fechado", proc.returncode is not None)
pump(until=lambda: guest.room is None)

# ---- túnel que falha: o usuário recebe uma mensagem clara e volta para o início
os.environ["FAKE_MODE"] = "crash"
host.on_host({"nick": "Kio", "folder": str(folder), "mode": "internet"})
ok = pump(until=lambda: host.room is None and "internet" in host.home.msg.text())
check("falha ao abrir o túnel mostra mensagem e volta ao início", ok)
host.close(); guest.close()
print(f"\n{len(PASS)} ok, {len(FAIL)} falharam")
sys.exit(1 if FAIL else 0)
