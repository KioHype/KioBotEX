"""Interface do KioBot EX: tela inicial, sala (soundboard + cronômetro) e janela principal."""
import asyncio
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QRadioButton,
                               QSlider, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

import protocol as P
import tunnel
from audio import AudioEngine
from net import Cache, GuestSession, HostSession, test_relay
from store import Board, Settings, app_dir
from widgets import (QSS, EditorPanel, SoundGrid, SoundTile, TimersTab, card, label)


def resource(rel):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / rel


def image_ok(data: bytes) -> bool:
    img = QImage()
    return bool(img.loadFromData(data)) and 0 < img.width() <= 4096 and 0 < img.height() <= 4096


# ======================================================================
#  Ponte entre a interface (Qt) e a rede (asyncio em outra thread)
# ======================================================================
class Runtime(QObject):
    event = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()
        self.session = self.relay = self._task = self.tunnel = None
        self.lan_port = self.public_host = None

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def emit(self, name, data):
        self.event.emit(name, data)

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, fn, *a):
        self.loop.call_soon_threadsafe(fn, *a)

    # ---- iniciar / parar
    async def _local_relay(self, host, trust_proxy):
        import relay
        for port in range(P.LAN_PORT, P.LAN_PORT + 11):
            try:
                server, _ = await relay.start_server(host, port, trust_proxy=trust_proxy)
                self.relay, self.lan_port = server, port
                return port
            except OSError:
                continue
        raise OSError("nenhuma porta livre para a sala")

    def start_host(self, mode, relay_url, nick, manifest, paths):
        """mode: 'lan' (mesma rede), 'tunnel' (internet automática) ou 'relay' (relé próprio)."""
        def status(text):
            self.emit("status", {"text": text, "level": "warn"})

        async def go():
            try:
                url = relay_url
                if mode == "lan":
                    url = f"ws://127.0.0.1:{await self._local_relay('0.0.0.0', False)}"
                elif mode == "tunnel":
                    status("Preparando a conexão com a internet...")
                    if not tunnel.is_installed():
                        def prog(done, total):
                            pct = f" {int(done * 100 / total)}%" if total else ""
                            status(f"Baixando o componente de internet (uma única vez){pct}")
                        await tunnel.install(prog)
                    elif not await asyncio.to_thread(tunnel.verify_installed):
                        await asyncio.to_thread(lambda: tunnel.binary_path().unlink(missing_ok=True))
                        await tunnel.install()
                    port = await self._local_relay("127.0.0.1", True)
                    status("Abrindo a conexão com a internet...")
                    self.tunnel = tunnel.Tunnel(port, self.emit)
                    self.public_host = (await self.tunnel.start())[len("https://"):]
                    url = f"ws://127.0.0.1:{port}"
                s = HostSession(url, nick, self.emit)
                s.manifest, s.paths = manifest, paths
                self.session = s
                self._task = asyncio.ensure_future(s.run())
            except tunnel.TunnelError as ex:
                await self._shutdown()
                self.emit("fatal", {"text": str(ex)})
            except Exception as ex:
                await self._shutdown()
                self.emit("fatal", {"text": f"Não foi possível criar a sala: {ex}"})
        self.submit(go())

    def start_guest(self, url, code, nick, cache):
        async def go():
            s = GuestSession(url, code, nick, cache, self.emit)
            self.session = s
            self._task = asyncio.ensure_future(s.run())
        self.submit(go())

    async def _shutdown(self):
        s, r, t, tn = self.session, self.relay, self._task, self.tunnel
        self.session = self.relay = self._task = self.tunnel = None
        self.public_host = None
        if tn:
            await tn.stop()
        if s:
            await s.close()
        if t:
            try:
                await asyncio.wait_for(t, 2)
            except Exception:
                t.cancel()
        if r:
            r.close()
            await r.wait_closed()

    def stop(self):
        try:
            self.submit(self._shutdown()).result(timeout=6)
        except Exception:
            pass

    def test_relay(self, url):
        async def go():
            ok, detail = await test_relay(url)
            self.emit("relay_test", {"ok": ok, "detail": detail})
        self.submit(go())

    # ---- ações da sala
    def play(self, sid):
        s = self.session
        if isinstance(s, HostSession):
            self.submit(s.play(sid, s.nick))
        elif s:
            self.submit(s.send_app({"k": "play", "sid": sid}))

    def timer_add(self, req):
        s = self.session
        if isinstance(s, HostSession):
            def go():
                ok, text = s.add_timer("host", s.nick, req["mode"], req["seconds"], req["times"], req["sid"])
                if not ok:
                    self.emit("notice", {"text": text})
            self.call(go)
        elif s:
            self.submit(s.send_app({"k": "timer_add", "mode": req["mode"], "seconds": req["seconds"],
                                    "times": req["times"], "sid": req["sid"]}))

    def timer_cancel(self, tid):
        s = self.session
        if isinstance(s, HostSession):
            self.call(s.cancel_timer, tid)
        elif s:
            self.submit(s.send_app({"k": "timer_cancel", "id": tid}))

    def cancel_all(self):
        if isinstance(self.session, HostSession):
            self.call(self.session.cancel_all_timers)

    def kick(self, pid):
        if isinstance(self.session, HostSession):
            self.submit(self.session.kick(pid))

    def update_board(self, manifest, paths):
        if isinstance(self.session, HostSession):
            self.call(self.session.update_board, manifest, paths)


# ======================================================================
#  Configurações
# ======================================================================
class SettingsDialog(QDialog):
    def __init__(self, st, rt, parent=None):
        super().__init__(parent)
        self.rt = rt
        self.setWindowTitle("Configurações")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(10)
        v.addWidget(label("Servidor próprio (avançado)", "h2"))
        v.addWidget(label("Deixe em branco: o programa cria sozinho a conexão com a internet. "
                          "Preencha só se você hospeda um relé próprio (ex.: wss://meu-servidor.exemplo.com). "
                          "O relé só repassa mensagens curtas e nunca vê ou guarda os seus sons.", "muted", wrap=True))
        self.url = QLineEdit(st["relay_url"])
        self.url.setPlaceholderText("wss://...")
        v.addWidget(self.url)
        tr = QHBoxLayout()
        self.test_btn = QPushButton("Testar conexão")
        self.test_btn.clicked.connect(self._test)
        tr.addWidget(self.test_btn)
        self.test_lbl = label("", "muted", wrap=True)
        tr.addWidget(self.test_lbl, 1)
        v.addLayout(tr)
        rt.event.connect(self._on_event)
        self.finished.connect(lambda _: rt.event.disconnect(self._on_event))
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = QPushButton("Cancelar"), QPushButton("Salvar")
        ok.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)


    def _test(self):
        url = P.normalize_relay_url(self.url.text())
        if not url:
            self._show("Campo em branco: o programa cria a conexão com a internet sozinho. "
                       "Não há nada para testar aqui.", "muted")
            return
        self.test_btn.setEnabled(False)
        self._show("Testando... (se o servidor estiver dormindo, pode levar até 1 minuto)", "muted")
        self.rt.test_relay(url)

    def _show(self, text, kind):
        self.test_lbl.setObjectName(kind)
        self.test_lbl.style().unpolish(self.test_lbl)
        self.test_lbl.style().polish(self.test_lbl)
        self.test_lbl.setText(text)

    def _on_event(self, name, data):
        if name != "relay_test":
            return
        self.test_btn.setEnabled(True)
        if data["ok"]:
            self._show(f"Conectou ao relé em {data['detail']} ms (tempo total, já com a criptografia).", "ok")
        else:
            self._show(data["detail"], "error")


# ======================================================================
#  Tela inicial
# ======================================================================
class HomePage(QWidget):
    host_requested = Signal(dict)
    join_requested = Signal(dict)
    settings_requested = Signal()

    def __init__(self, st):
        super().__init__()
        self.st = st
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 32, 48, 24)
        outer.setSpacing(18)

        top = QHBoxLayout()
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label("KioBot EX", "h1"))
        tv.addWidget(label("Soundboard compartilhado em tempo real, para usar junto com a sua call.", "muted"))
        top.addLayout(tv, 1)
        sb = QPushButton("Configurações")
        sb.clicked.connect(self.settings_requested)
        top.addWidget(sb, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)

        nr = QHBoxLayout()
        nr.addWidget(label("Seu apelido"))
        self.nick = QLineEdit(st["nick"])
        self.nick.setMaxLength(P.MAX_NICK_LEN)
        self.nick.setPlaceholderText("Como você quer aparecer na sala (opcional)")
        self.nick.setMaximumWidth(420)
        nr.addWidget(self.nick, 1)
        nr.addStretch(1)
        outer.addLayout(nr)

        cards = QHBoxLayout()
        cards.setSpacing(20)

        # ---- hospedar
        h = card()
        hv = QVBoxLayout(h)
        hv.setContentsMargins(24, 22, 24, 22)
        hv.setSpacing(8)
        hv.addWidget(label("Hospedar uma sala", "h2"))
        hv.addWidget(label("A sala roda no seu computador. Os sons da sua pasta ficam disponíveis "
                           "para quem entrar, e todo mundo ouve junto.", "muted", wrap=True))
        hv.addSpacing(6)
        hv.addWidget(label("Pasta de sons", "muted"))
        fr = QHBoxLayout()
        self.folder_lbl = label("", wrap=True)
        fr.addWidget(self.folder_lbl, 1)
        fb = QPushButton("Escolher pasta...")
        fb.clicked.connect(self.choose_folder)
        fr.addWidget(fb)
        hv.addLayout(fr)
        hv.addSpacing(6)
        hv.addWidget(label("Quem vai entrar?", "muted"))
        self.r_net = QRadioButton("Pessoas em qualquer lugar (pela internet)")
        self.r_lan = QRadioButton("Pessoas na mesma rede (mesma casa ou Wi-Fi)")
        (self.r_lan if st["room_mode"] == "lan" else self.r_net).setChecked(True)
        hv.addWidget(self.r_net)
        hv.addWidget(label("Na primeira vez baixa um componente oficial da Cloudflare, sem conta nem configuração.",
                           "muted", wrap=True))
        hv.addWidget(self.r_lan)
        hv.addStretch(1)
        hv.addSpacing(14)
        go = QPushButton("Criar sala")
        go.setObjectName("primary")
        go.clicked.connect(self._host)
        hv.addWidget(go)
        cards.addWidget(h, 1)

        # ---- entrar
        j = card()
        jv = QVBoxLayout(j)
        jv.setContentsMargins(24, 22, 24, 22)
        jv.setSpacing(8)
        jv.addWidget(label("Entrar em uma sala", "h2"))
        jv.addWidget(label("Cole o convite que o host enviou para você. É só isso.", "muted", wrap=True))
        jv.addSpacing(6)
        jv.addWidget(label("Convite ou código da sala", "muted"))
        self.invite = QLineEdit()
        self.invite.setPlaceholderText("Cole aqui o convite que o host enviou")
        self.invite.returnPressed.connect(self._join)
        jv.addWidget(self.invite)
        jv.addStretch(1)
        jv.addSpacing(14)
        jb = QPushButton("Entrar na sala")
        jb.setObjectName("primary")
        jb.clicked.connect(self._join)
        jv.addWidget(jb)
        cards.addWidget(j, 1)
        outer.addLayout(cards)
        outer.addStretch(1)

        self.msg = label("", "error", wrap=True)
        outer.addWidget(self.msg)
        outer.addWidget(label(f"Os sons tocam no seu computador, sem passar pela sua call. "
                              f"Não precisa de conta, senha nem dados pessoais.   \u00B7   v{P.VERSION}", "muted", wrap=True))
        self.refresh()

    def refresh(self):
        f = self.st["folder"]
        self.folder_lbl.setText(f if f else "Nenhuma pasta escolhida")

    def show_message(self, text, ok=False):
        self.msg.setObjectName("ok" if ok else "error")
        self.msg.style().unpolish(self.msg)
        self.msg.style().polish(self.msg)
        self.msg.setText(text)

    def choose_folder(self):
        start = self.st["folder"] or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Escolha a pasta com os seus sons", start)
        if d:
            self.st["folder"] = d
            self.refresh()

    def _save_prefs(self):
        self.st["nick"] = self.nick.text().strip()
        self.st["room_mode"] = "internet" if self.r_net.isChecked() else "lan"

    def _host(self):
        self._save_prefs()
        if not self.st["folder"] or not Path(self.st["folder"]).is_dir():
            self.choose_folder()
            if not self.st["folder"]:
                return
        self.show_message("")
        self.host_requested.emit({"nick": self.nick.text().strip(), "folder": self.st["folder"],
                                  "mode": self.st["room_mode"]})

    def _join(self):
        self._save_prefs()
        if not self.invite.text().strip():
            self.show_message("Cole o código da sala para entrar.")
            return
        self.show_message("")
        self.join_requested.emit({"nick": self.nick.text().strip(), "invite": self.invite.text()})


# ======================================================================
#  Sala
# ======================================================================
class RoomPage(QWidget):
    leave_requested = Signal()

    def __init__(self, rt, st, audio, is_host, nick, board=None, cache=None, mode="lan"):
        super().__init__()
        self.rt, self.st, self.audio, self.is_host, self.nick = rt, st, audio, is_host, nick
        self.board, self.cache, self.mode = board, cache, mode
        self.paths, self.entries, self.sel, self.edit = {}, [], -1, False
        self.invite_text, self.my_id, self._pm = "", ("host" if is_host else ""), {}
        self.setAcceptDrops(is_host)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- barra superior
        top = QFrame()
        top.setObjectName("topbar")
        t = QHBoxLayout(top)
        t.setContentsMargins(16, 10, 16, 10)
        t.setSpacing(10)
        self.title = label("Sala", "h2")
        t.addWidget(self.title)
        self.code = label("········", "code")
        self.code.setMinimumWidth(220)
        t.addWidget(self.code)
        self.copy_btn = QPushButton("Copiar convite")
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.setEnabled(False)
        t.addWidget(self.copy_btn)
        self.status = label("Conectando...", "muted")
        t.addWidget(self.status)
        t.addStretch(1)
        leave = QPushButton("Encerrar sala" if is_host else "Sair da sala")
        leave.setObjectName("danger")
        leave.clicked.connect(self._leave)
        t.addWidget(leave)
        root.addWidget(top)

        # ---- corpo
        body = QHBoxLayout()
        body.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(250)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(14, 14, 14, 14)
        sv.setSpacing(6)
        self.users_title = label("Na sala (0)", "muted")
        sv.addWidget(self.users_title)
        self.users_box = QVBoxLayout()
        self.users_box.setSpacing(6)
        sv.addLayout(self.users_box)
        sv.addStretch(1)
        sv.addWidget(label("Volume (só para você)", "muted"))
        vr = QHBoxLayout()
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(st["volume"]))
        self.vol.valueChanged.connect(self._volume)
        vr.addWidget(self.vol, 1)
        self.vol_lbl = label(f'{int(st["volume"])}%')
        self.vol_lbl.setFixedWidth(42)
        vr.addWidget(self.vol_lbl)
        sv.addLayout(vr)
        br = QHBoxLayout()
        self.mute = QPushButton("Silenciar")
        self.mute.setCheckable(True)
        self.mute.toggled.connect(self._mute)
        stop = QPushButton("Parar sons")
        stop.setToolTip("Para os sons que estão tocando agora, só no seu computador")
        stop.clicked.connect(audio.stop_all)
        br.addWidget(self.mute)
        br.addWidget(stop)
        sv.addLayout(br)
        body.addWidget(side)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.addTab(self._build_board_tab(), "Soundboard")
        self.timers = TimersTab(is_host)
        self.timers.my_id = self.my_id
        self.timers.create_requested.connect(self._timer_add)
        self.timers.cancel_requested.connect(rt.timer_cancel)
        self.timers.cancel_all_requested.connect(rt.cancel_all)
        self.tabs.addTab(self.timers, "Cronômetro")
        body.addWidget(self.tabs, 1)
        root.addLayout(body, 1)

        # ---- rodapé
        foot = QFrame()
        foot.setObjectName("topbar")
        f = QHBoxLayout(foot)
        f.setContentsMargins(16, 6, 16, 6)
        self.notice = label("", "muted")
        f.addWidget(self.notice, 1)
        self.now = label("Nenhum som tocou ainda", "muted")
        f.addWidget(self.now)
        root.addWidget(foot)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(lambda: self.notice.setText(""))
        self._dirty = QTimer(self)
        self._dirty.setSingleShot(True)
        self._dirty.timeout.connect(self.rebuild_tiles)

        if is_host:
            self.title.setText("Sua sala")
            self.refresh_board(push=False)
            if self.board.skipped:
                self._say(f"{len(self.board.skipped)} arquivo(s) da pasta foram ignorados: "
                          + "; ".join(f"{n} ({r})" for n, r in self.board.skipped[:3]))
        else:
            self.grid.set_empty_text("Aguardando a lista de sons do host...")

    # ---------- construção ----------
    def _build_board_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 14, 0, 0)
        v.setSpacing(10)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 16, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar som...")
        self.search.setMaximumWidth(280)
        self.search.textChanged.connect(lambda t: self.grid.set_filter(t))
        bar.addWidget(self.search)
        bar.addStretch(1)
        if self.is_host:
            self.btn_add = QPushButton("Adicionar sons...")
            self.btn_open = QPushButton("Abrir pasta")
            self.btn_scan = QPushButton("Atualizar pasta")
            self.btn_add.clicked.connect(self._add_sounds)
            self.btn_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.board.folder))))
            self.btn_scan.clicked.connect(self._rescan)
            self.btn_edit = QPushButton("Editar soundboard")
            self.btn_edit.setCheckable(True)
            self.btn_edit.toggled.connect(self._toggle_edit)
            for b in (self.btn_add, self.btn_open, self.btn_scan):
                b.hide()
                bar.addWidget(b)
            bar.addWidget(self.btn_edit)
        v.addLayout(bar)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress_lbl = label("", "muted")
        self.progress.hide()
        self.progress_lbl.hide()
        pr = QHBoxLayout()
        pr.setContentsMargins(0, 0, 16, 0)
        pr.addWidget(self.progress_lbl)
        pr.addWidget(self.progress, 1)
        v.addLayout(pr)
        row = QHBoxLayout()
        row.setSpacing(0)
        self.grid = SoundGrid()
        self.grid.tile_clicked.connect(self._tile)
        row.addWidget(self.grid, 1)
        self.editor = EditorPanel()
        self.editor.hide()
        if self.is_host:
            self.editor.renamed.connect(self._rename)
            self.editor.recolored.connect(self._recolor)
            self.editor.image_picked.connect(self._image)
            self.editor.image_cleared.connect(self._image_clear)
            self.editor.moved.connect(self._move)
            self.editor.removed.connect(self._remove)
            self.editor.tested.connect(self._test)
        row.addWidget(self.editor)
        v.addLayout(row, 1)
        return w

    # ---------- caminhos de arquivo ----------
    def sound_path(self, sid):
        if self.is_host:
            return self.paths.get(("snd", sid))
        return self.cache.path("snd", sid)

    def image_path(self, iid):
        if self.is_host:
            return self.paths.get(("img", iid))
        return self.cache.path("img", iid)

    # ---------- soundboard ----------
    def refresh_board(self, push=True):
        manifest, paths = self.board.snapshot()
        self.entries, self.paths = manifest, paths
        self.rebuild_tiles()
        self.timers.set_sounds(self.entries)
        if self.is_host:
            self.grid.set_empty_text("Nenhum som ainda. Clique em \"Editar soundboard\" e depois em "
                                     "\"Adicionar sons...\", ou arraste arquivos de áudio para esta janela.")
        if push:
            self.rt.update_board(manifest, paths)

    def rebuild_tiles(self):
        tiles = []
        for i, e in enumerate(self.entries):
            pm = None
            if e["iid"]:
                p = self.image_path(e["iid"])
                if p:
                    pm = self._pm.get(str(p)) or self._pm.setdefault(str(p), QPixmap(str(p)))
            ready = self.sound_path(e["sid"]) is not None
            tiles.append(SoundTile(i, e, pm, ready, self.edit, self.edit and i == self.sel))
        self.grid.set_tiles(tiles)

    def _tile(self, i):
        if self.edit and self.is_host:
            self.editor.flush()
            self.sel = i
            self.editor.show_entry(self.board.entries[i])
            for t in self.grid.tiles:
                t.selected = t.index == i
                t._style()
        else:
            self.rt.play(self.entries[i]["sid"])

    def _toggle_edit(self, on):
        self.edit = on
        self.btn_edit.setText("Concluir edição" if on else "Editar soundboard")
        for b in (self.btn_add, self.btn_open, self.btn_scan):
            b.setVisible(on)
        self.editor.setVisible(on)
        if not on:
            self.editor.flush()
            self.sel = -1
            self.editor.clear()
        self.rebuild_tiles()

    def _valid_sel(self):
        return 0 <= self.sel < len(self.board.entries)

    def _rename(self, text):
        if self._valid_sel():
            self.board.rename(self.sel, text)
            self.refresh_board()

    def _recolor(self, color):
        if self._valid_sel():
            self.board.recolor(self.sel, color)
            self.refresh_board()

    def _image(self, path):
        if self._valid_sel():
            if not self.board.set_image(self.sel, path):
                self._say("Não consegui usar essa imagem.")
            self.refresh_board()

    def _image_clear(self):
        if self._valid_sel():
            self.board.clear_image(self.sel)
            self.refresh_board()

    def _move(self, delta):
        if self._valid_sel():
            self.editor.flush()
            self.sel = self.board.move(self.sel, delta)
            self.refresh_board()

    def _remove(self):
        if self._valid_sel():
            self.board.remove(self.sel)
            self.sel = -1
            self.editor.clear()
            self.refresh_board()

    def _test(self):
        if self._valid_sel() and self.sel < len(self.entries):
            p = self.sound_path(self.entries[self.sel]["sid"])
            if p and not self.audio.play(p):
                self._say("Não encontrei nenhuma saída de áudio neste computador.")

    def _add_sounds(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Adicionar sons", "", "Áudio (*.mp3 *.wav *.ogg)")
        if files:
            self._after_add(self.board.add_files(files))

    def _rescan(self):
        self.board.load()
        self._after_add(None)

    def _after_add(self, added):
        self.refresh_board()
        parts = []
        if added:
            parts.append(f"{added} som(ns) adicionado(s).")
        if self.board.skipped:
            parts.append(f"{len(self.board.skipped)} arquivo(s) ignorado(s): "
                         + "; ".join(f"{n} ({r})" for n, r in self.board.skipped[:3]))
        self._say(" ".join(parts) or "Pasta atualizada.")

    def dragEnterEvent(self, e):
        if self.is_host and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            self._after_add(self.board.add_files(files))

    # ---------- barra superior ----------
    def set_code(self, code, invite):
        self.code.setText(code)
        self.invite_text = invite
        self.copy_btn.setEnabled(True)

    def _copy(self):
        QApplication.clipboard().setText(self.invite_text)
        self._say("Convite copiado. Envie para quem vai entrar.")

    def _volume(self, v):
        self.vol_lbl.setText(f"{v}%")
        self.audio.set_volume(v / 100)
        self.st["volume"] = v

    def _mute(self, on):
        self.audio.set_muted(on)
        self.mute.setText("Ativar som" if on else "Silenciar")

    def _leave(self):
        if self.is_host:
            box = QMessageBox(self)
            box.setWindowTitle("Encerrar sala")
            box.setText("Isso encerra a sala para todo mundo. Deseja continuar?")
            yes = box.addButton("Encerrar sala", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Voltar", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                return
        self.leave_requested.emit()

    def _timer_add(self, req):
        self.rt.timer_add(req)

    def _say(self, text):
        self.notice.setText(text)
        self._notice_timer.start(7000)

    def set_status(self, text, level="ok"):
        color = {"ok": "#57d28a", "warn": "#f0b232", "err": "#ff7b85"}.get(level, "#98a2d8")
        self.status.setText(f'<span style="color:{color}">\u25CF</span>&nbsp;{text}')

    # ---------- eventos da rede ----------
    def _set_users(self, users):
        while self.users_box.count():
            it = self.users_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for u in users:
            row = QFrame()
            row.setObjectName("row")
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 6, 8, 6)
            name = u["nick"] + ("  (host)" if u["host"] else "") + ("  (você)" if u["id"] == self.my_id and not u["host"] else "")
            lb = label(name)
            h.addWidget(lb, 1)
            if self.is_host and not u["host"]:
                b = QPushButton("Expulsar")
                b.setObjectName("chip")
                b.clicked.connect(lambda _=False, pid=u["id"]: self.rt.kick(pid))
                h.addWidget(b)
            self.users_box.addWidget(row)
        self.users_title.setText(f"Na sala ({len(users)})")

    def handle(self, name, d):
        if name == "hosted":
            code = P.format_code(d["code"])
            if self.mode == "lan":
                invite = f"{P.local_ip()}:{self.rt.lan_port}/{d['code']}"
                self.set_code(code, invite)
                self.set_status(f"Sala aberta na rede local ({P.local_ip()}:{self.rt.lan_port})")
            elif self.mode == "tunnel":
                self.set_code(code, f"{self.rt.public_host}/{d['code']}")
                self.set_status("Sala aberta na internet")
            else:
                url = self.st["relay_url"].rstrip("/")
                self.set_code(code, f"{url}/{d['code']}")
                self.set_status("Sala aberta na internet (relé próprio)")
        elif name == "joined":
            self.my_id = d["id"]
            self.timers.my_id = d["id"]
            self.set_status("Conectado")
        elif name == "host_nick":
            self.title.setText(f'Sala de {d["nick"]}')
        elif name == "users":
            self._set_users(d["list"])
        elif name == "timers":
            self.timers.set_timers(d["list"])
        elif name == "board":
            self.entries = d["board"]
            self.rebuild_tiles()
            self.timers.set_sounds(self.entries)
            self.grid.set_empty_text("O host ainda não adicionou sons.")
        elif name == "file_ready":
            self._dirty.start(150)
        elif name == "progress":
            done, total = d["done"], d["total"]
            show = total > 0 and done < total
            self.progress.setVisible(show)
            self.progress_lbl.setVisible(show)
            if show:
                self.progress.setRange(0, total)
                self.progress.setValue(done)
                self.progress_lbl.setText(f"Baixando sons: {done}/{total}")
        elif name == "play":
            p = self.sound_path(d["sid"])
            if p and not self.audio.play(p):
                self._say("Não encontrei nenhuma saída de áudio neste computador.")
            tile = self.grid.tile_by_sid(d["sid"])
            if tile:
                tile.flash()
                self.now.setText(f'{tile.entry["name"]}  \u00B7  {d["by"]}')
        elif name == "status":
            self.set_status(d["text"], d.get("level", "ok"))
        elif name == "notice":
            self._say(d["text"])


# ======================================================================
#  Janela principal
# ======================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KioBot EX")
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)
        icon = resource("assets/icon.png")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.st = Settings()
        self.audio = AudioEngine(int(self.st["volume"]) / 100, self)
        self.rt = Runtime()
        self.rt.event.connect(self.on_event)
        self.cache = Cache(app_dir() / "cache", image_ok)
        self.cache.prune()
        tunnel.kill_stale()
        self.room = None
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home = HomePage(self.st)
        self.home.host_requested.connect(self.on_host)
        self.home.join_requested.connect(self.on_join)
        self.home.settings_requested.connect(self.on_settings)
        self.stack.addWidget(self.home)

    def on_settings(self):
        dlg = SettingsDialog(self.st, self.rt, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            url = P.normalize_relay_url(dlg.url.text())
            if P.is_temporary_tunnel(url):
                QMessageBox.information(self, "Endereço temporário",
                                        "Endereços .trycloudflare.com são temporários e morrem quando o túnel "
                                        "fecha. Você não precisa mais configurar isso: o programa cria a conexão "
                                        "com a internet sozinho. O campo foi deixado em branco.")
                url = ""
            self.st["relay_url"] = url

    def _open_room(self, room):
        self.room = room
        room.leave_requested.connect(self.leave)
        self.stack.addWidget(room)
        self.stack.setCurrentWidget(room)

    def on_host(self, req):
        mode, url = req["mode"], self.st["relay_url"]
        if mode == "internet":
            if url:
                mode = "relay"
            else:
                if not tunnel.supported():
                    self.home.show_message("Salas pela internet automáticas ainda não funcionam neste sistema. "
                                           "Use \"mesma rede\" ou informe um relé próprio em Configurações.")
                    return
                if not tunnel.is_installed() and not self._ask_download():
                    return
                mode = "tunnel"
        else:
            mode = "lan"
        try:
            board = Board(req["folder"])
        except OSError as ex:
            self.home.show_message(f"Não consegui abrir a pasta: {ex}")
            return
        nick = req["nick"] or "Host"
        manifest, paths = board.snapshot()
        self._open_room(RoomPage(self.rt, self.st, self.audio, True, nick, board=board, mode=mode))
        self.rt.start_host(mode, url, nick, manifest, paths)

    def _ask_download(self):
        box = QMessageBox(self)
        box.setWindowTitle("Conexão com a internet")
        box.setText(f"Para criar salas pela internet, o programa precisa baixar o cloudflared, o programa "
                    f"oficial da Cloudflare (cerca de {tunnel.size_mb()} MB), de github.com/cloudflare/cloudflared. "
                    "Isso acontece uma única vez, e o arquivo é conferido antes de ser usado.\n\n"
                    "O serviço gratuito da Cloudflare não tem garantia de funcionamento.\n\nBaixar e continuar?")
        yes = box.addButton("Baixar e continuar", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def on_join(self, req):
        url, code = P.parse_invite(req["invite"])
        if not code:
            self.home.show_message("Esse código não parece válido. Ele tem 8 letras e números (ex.: ABCD-2345).")
            return
        if url is None:
            url = self.st["relay_url"]
            if not url:
                self.home.show_message("Esse código precisa do servidor da internet, que ainda não foi "
                                       "configurado. Peça ao host o convite de rede local ou configure "
                                       "o servidor em Configurações.")
                return
        nick = req["nick"] or "Convidado"
        room = RoomPage(self.rt, self.st, self.audio, False, nick, cache=self.cache)
        room.set_code(P.format_code(code), req["invite"])
        self._open_room(room)
        self.rt.start_guest(url, code, nick, self.cache)

    def leave(self, message=None, ok=False):
        self.rt.stop()
        self.audio.stop_all()
        if self.room:
            self.stack.removeWidget(self.room)
            self.room.deleteLater()
            self.room = None
        self.stack.setCurrentWidget(self.home)
        self.home.show_message(message or "", ok)

    def on_event(self, name, data):
        if name == "fatal":
            self.leave(data["text"])
        elif name == "closed":
            self.leave(data["text"], ok=True)
        elif name == "tunnel_down":
            self.leave("A conexão com a internet foi encerrada. Crie a sala novamente.")
        elif self.room:
            self.room.handle(name, data)

    def closeEvent(self, e):
        self.rt.stop()
        super().closeEvent(e)


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("KioBot EX")
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    return app.exec()
