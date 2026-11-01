"""Tema e componentes visuais do KioBot EX (azul escuro com toques de roxo)."""
import time
from string import Template

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

import protocol as P
from store import PALETTE

C = dict(BG0="#0e1226", BG1="#141a35", BG2="#1b2245", BG3="#232b57", LINE="#2e3868",
         TXT="#e8ebff", MUTED="#98a2d8", BLURPLE="#5865f2", PURPLE="#8b5cf6")

QSS = Template("""
QMainWindow, QDialog { background: $BG0; }
* { color: $TXT; font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 14px; }
QLabel { background: transparent; }
QWidget#gridInner { background: transparent; }
QToolTip { background: $BG3; color: $TXT; border: 1px solid $LINE; padding: 4px; }
QFrame#card { background: $BG2; border: 1px solid $LINE; border-radius: 14px; }
QFrame#row { background: $BG2; border: 1px solid $LINE; border-radius: 10px; }
QFrame#topbar { background: $BG1; border-bottom: 1px solid $LINE; }
QFrame#sidebar { background: $BG1; border-right: 1px solid $LINE; }
QFrame#panel { background: $BG1; border-left: 1px solid $LINE; }
QPushButton { background: $BG3; border: 1px solid $LINE; border-radius: 8px; padding: 8px 14px; }
QPushButton:hover { background: #2d3873; }
QPushButton:pressed { background: #36418a; }
QPushButton:disabled { color: $MUTED; background: $BG2; }
QPushButton#primary { background: $BLURPLE; border: none; font-weight: 600; padding: 11px 20px; }
QPushButton#primary:hover { background: #6d79ff; }
QPushButton#primary:disabled { background: #3a4170; color: #9aa3d6; }
QPushButton#danger { background: #6e2434; border: 1px solid #9c3449; }
QPushButton#danger:hover { background: #9c3449; }
QPushButton#seg { background: $BG1; }
QPushButton#seg:checked { background: $PURPLE; border: none; font-weight: 600; }
QPushButton#chip { padding: 4px 11px; border-radius: 12px; background: $BG1; }
QPushButton#chip:hover { background: $BLURPLE; }
QLineEdit, QSpinBox, QComboBox { background: $BG1; border: 1px solid $LINE; border-radius: 8px;
    padding: 7px 10px; selection-background-color: $BLURPLE; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid $BLURPLE; }
QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; background: transparent; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: $BG1; border: 1px solid $LINE;
    selection-background-color: $BLURPLE; outline: 0; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: $MUTED; padding: 11px 24px; margin-right: 4px;
    border-bottom: 3px solid transparent; font-weight: 600; font-size: 15px; }
QTabBar::tab:selected { color: $TXT; border-bottom: 3px solid $PURPLE; }
QTabBar::tab:hover { color: $TXT; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: $LINE; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3f4b8a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSlider::groove:horizontal { height: 6px; background: $LINE; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $PURPLE; border-radius: 3px; }
QSlider::handle:horizontal { background: #ffffff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QProgressBar { background: $BG1; border: none; border-radius: 6px; max-height: 12px; text-align: center; color: transparent; }
QProgressBar::chunk { background: $BLURPLE; border-radius: 6px; }
QRadioButton { spacing: 8px; background: transparent; }
QRadioButton::indicator { width: 14px; height: 14px; border-radius: 9px; border: 2px solid $LINE; background: $BG1; }
QRadioButton::indicator:checked { background: $PURPLE; border: 2px solid $PURPLE; }
QLabel#h1 { font-size: 32px; font-weight: 700; }
QLabel#h2 { font-size: 18px; font-weight: 700; }
QLabel#muted { color: $MUTED; }
QLabel#error { color: #ff7b85; }
QLabel#ok { color: #57d28a; }
QLabel#code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 20px; font-weight: 700;
    letter-spacing: 3px; background: $BG0; border: 1px solid $LINE; border-radius: 8px; padding: 6px 12px; }
QLabel#clock { font-family: Consolas, "Cascadia Mono", monospace; font-size: 22px; font-weight: 700; }
""").safe_substitute(C)


def label(text="", name=None, wrap=False):
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def card():
    f = QFrame()
    f.setObjectName("card")
    return f


def fmt_time(s):
    s = max(0, int(s + 0.999))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# ======================================================================
#  Botão do soundboard
# ======================================================================
class SoundTile(QFrame):
    clicked = Signal(int)
    SIZE = 150

    def __init__(self, index, entry, pixmap=None, ready=True, edit=False, selected=False):
        super().__init__()
        self.setObjectName("tile")
        self.index, self.entry, self.ready, self.edit, self.selected = index, entry, ready, edit, selected
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor if (ready or edit) else Qt.CursorShape.ArrowCursor)
        self.setToolTip(entry["name"] if ready else f'{entry["name"]} (baixando...)')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 12, 8, 8)
        lay.setSpacing(4)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setFixedHeight(82)
        if pixmap is not None and not pixmap.isNull() and ready:
            self.icon.setPixmap(pixmap.scaled(78, 78, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.icon.setText("\u25B6" if ready else "\u2026")
            self.icon.setStyleSheet("font-size: 34px; color: rgba(255,255,255,0.55);")
        self.label = QLabel(entry["name"])
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.label.setFixedHeight(40)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        self._style()

    def _style(self, flash=False):
        c = QColor(self.entry["color"])
        if not self.ready:
            c = QColor.fromRgbF(c.redF() * 0.45 + 0.05, c.greenF() * 0.45 + 0.07, c.blueF() * 0.45 + 0.12)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        fg = "#ffffff" if lum < 150 else "#10131f"
        if flash:
            border = "3px solid #ffffff"
        elif self.selected:
            border = "3px solid #ffffff"
        elif self.edit:
            border = "2px dashed rgba(255,255,255,0.65)"
        else:
            border = "2px solid rgba(255,255,255,0.10)"
        self.setStyleSheet(f"QFrame#tile {{ background: {c.name()}; border: {border}; border-radius: 16px; }}"
                           f"QFrame#tile QLabel {{ color: {fg}; background: transparent; font-weight: 600; }}")

    def flash(self):
        self._style(flash=True)
        QTimer.singleShot(220, self._style)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and (self.ready or self.edit):
            self.clicked.emit(self.index)


class SoundGrid(QScrollArea):
    tile_clicked = Signal(int)
    GAP = 12

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("gridInner")
        self.grid = QGridLayout(self.inner)
        self.grid.setSpacing(self.GAP)
        self.grid.setContentsMargins(self.GAP, self.GAP, self.GAP, self.GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self.inner)
        self.tiles, self._filter, self._cols = [], "", 0
        self.empty = label("", "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(160)
        self.empty_text = ""

    def set_empty_text(self, text):
        self.empty_text = text
        self._layout()

    def set_tiles(self, tiles):
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = tiles
        for t in tiles:
            t.clicked.connect(self.tile_clicked)
            t.setParent(self.inner)
        self._layout()

    def set_filter(self, text):
        self._filter = text.strip().lower()
        self._layout()

    def tile_by_sid(self, sid):
        return next((t for t in self.tiles if t.entry.get("sid") == sid), None)

    def _columns(self):
        return max(1, (self.viewport().width() - self.GAP) // (SoundTile.SIZE + self.GAP))

    def _layout(self):
        while self.grid.count():
            self.grid.takeAt(0)
        cols = self._cols = self._columns()
        r = c = 0
        shown = 0
        for t in self.tiles:
            vis = self._filter in t.entry["name"].lower()
            t.setVisible(vis)
            if vis:
                self.grid.addWidget(t, r, c)
                shown += 1
                c += 1
                if c >= cols:
                    c, r = 0, r + 1
        if shown == 0:
            self.empty.setText("Nenhum som encontrado." if self.tiles else self.empty_text)
            self.grid.addWidget(self.empty, 0, 0, 1, max(1, cols))
            self.empty.show()
        else:
            self.empty.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._columns() != self._cols:
            self._layout()


# ======================================================================
#  Painel de edição do botão (só o host)
# ======================================================================
class EditorPanel(QFrame):
    renamed = Signal(str)
    recolored = Signal(str)
    image_picked = Signal(str)
    image_cleared = Signal()
    moved = Signal(int)
    removed = Signal()
    tested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setFixedWidth(300)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)
        v.addWidget(label("Editar botão", "h2"))
        self.hint = label("Clique em um botão da soundboard para editá-lo.", "muted", wrap=True)
        v.addWidget(self.hint)
        self.form = QWidget()
        f = QVBoxLayout(self.form)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(8)
        f.addWidget(label("Nome (aparece embaixo do botão)", "muted"))
        self.name = QLineEdit()
        self.name.setMaxLength(P.MAX_NAME_LEN)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.flush)
        self.name.textEdited.connect(lambda _: self._timer.start(500))
        f.addWidget(self.name)
        self.file_lbl = label("", "muted", wrap=True)
        f.addWidget(self.file_lbl)
        f.addWidget(label("Cor", "muted"))
        sw = QGridLayout()
        sw.setSpacing(6)
        for i, col in enumerate(PALETTE):
            b = QPushButton()
            b.setFixedSize(30, 30)
            b.setStyleSheet(f"QPushButton {{ background: {col}; border: 2px solid rgba(255,255,255,0.18); border-radius: 15px; padding: 0; }}"
                            "QPushButton:hover { border: 2px solid #ffffff; }")
            b.clicked.connect(lambda _=False, c=col: self.recolored.emit(c))
            sw.addWidget(b, i // 5, i % 5)
        f.addLayout(sw)
        other = QPushButton("Outra cor...")
        other.clicked.connect(self._pick_color)
        f.addWidget(other)
        f.addWidget(label("Imagem (PNG ou JPG)", "muted"))
        row = QHBoxLayout()
        pick = QPushButton("Escolher imagem...")
        pick.clicked.connect(self._pick_image)
        clear = QPushButton("Remover")
        clear.clicked.connect(self.image_cleared)
        row.addWidget(pick, 1)
        row.addWidget(clear)
        f.addLayout(row)
        f.addWidget(label("Posição", "muted"))
        row2 = QHBoxLayout()
        left, right = QPushButton("\u25C0  Antes"), QPushButton("Depois  \u25B6")
        left.clicked.connect(lambda: self.moved.emit(-1))
        right.clicked.connect(lambda: self.moved.emit(1))
        row2.addWidget(left)
        row2.addWidget(right)
        f.addLayout(row2)
        test = QPushButton("Tocar para testar")
        test.clicked.connect(self.tested)
        f.addWidget(test)
        rem = QPushButton("Remover da soundboard")
        rem.setObjectName("danger")
        rem.setToolTip("Tira o botão da soundboard. O arquivo continua na sua pasta.")
        rem.clicked.connect(self.removed)
        f.addWidget(rem)
        v.addWidget(self.form)
        v.addStretch(1)
        self.form.hide()

    def flush(self):
        if self._timer.isActive():
            self._timer.stop()
        if self.form.isVisible():
            self.renamed.emit(self.name.text())

    def show_entry(self, entry):
        self.name.setText(entry["name"])
        self.file_lbl.setText(f'Arquivo: {entry["file"]}')
        self.hint.hide()
        self.form.show()

    def clear(self):
        self.form.hide()
        self.hint.show()

    def _pick_color(self):
        col = QColorDialog.getColor(parent=self, title="Escolha a cor do botão")
        if col.isValid():
            self.recolored.emit(col.name())

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Escolha uma imagem", "", "Imagens (*.png *.jpg *.jpeg)")
        if path:
            self.image_picked.emit(path)


# ======================================================================
#  Aba de cronômetros
# ======================================================================
class TimerRow(QFrame):
    cancel = Signal(str)

    def __init__(self, t, sound_name, can_cancel):
        super().__init__()
        self.setObjectName("row")
        self.t = t
        self.ends = time.monotonic() + t["remaining"]
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 10, 12, 10)
        v = QVBoxLayout()
        v.setSpacing(2)
        if t["kind"] == "once":
            title = "Uma vez"
        else:
            title = f'A cada {int(t["interval"] // 60)} min  \u00B7  restam {t["left"]} de {t["total"]}'
        v.addWidget(label(title))
        v.addWidget(label(f'por {t["by"]}  \u00B7  som: {sound_name}', "muted"))
        h.addLayout(v, 1)
        self.clock = label(fmt_time(t["remaining"]), "clock")
        h.addWidget(self.clock)
        if can_cancel:
            b = QPushButton("Cancelar")
            b.clicked.connect(lambda: self.cancel.emit(t["id"]))
            h.addWidget(b)

    def tick(self):
        self.clock.setText(fmt_time(self.ends - time.monotonic()))


class TimersTab(QWidget):
    create_requested = Signal(dict)
    cancel_requested = Signal(str)
    cancel_all_requested = Signal()

    def __init__(self, is_host):
        super().__init__()
        self.is_host, self.my_id, self.names, self.rows = is_host, "host" if is_host else "", {}, []
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ---- criar
        left = card()
        left.setFixedWidth(400)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(20, 18, 20, 18)
        lv.setSpacing(10)
        lv.addWidget(label("Novo cronômetro", "h2"))
        seg = QHBoxLayout()
        self.b_once, self.b_rep = QPushButton("Uma vez"), QPushButton("Repetir")
        for b in (self.b_once, self.b_rep):
            b.setObjectName("seg")
            b.setCheckable(True)
            seg.addWidget(b)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self.b_once, 0)
        grp.addButton(self.b_rep, 1)
        self.b_once.setChecked(True)
        lv.addLayout(seg)
        self.stack = QStackedWidget()
        grp.idClicked.connect(self.stack.setCurrentIndex)
        # uma vez
        p1 = QWidget()
        v1 = QVBoxLayout(p1)
        v1.setContentsMargins(0, 4, 0, 0)
        v1.addWidget(label("Toca um som depois de:", "muted"))
        r = QHBoxLayout()
        self.sp_h, self.sp_m, self.sp_s = QSpinBox(), QSpinBox(), QSpinBox()
        for sp, mx, suf in ((self.sp_h, 23, " h"), (self.sp_m, 59, " min"), (self.sp_s, 59, " s")):
            sp.setRange(0, mx)
            sp.setSuffix(suf)
            r.addWidget(sp)
        self.sp_m.setValue(5)
        v1.addLayout(r)
        ch = QGridLayout()
        ch.setSpacing(6)
        for i, (text, secs) in enumerate((("5 min", 300), ("10 min", 600), ("20 min", 1200),
                                          ("30 min", 1800), ("45 min", 2700), ("1 hora", 3600))):
            b = QPushButton(text)
            b.setObjectName("chip")
            b.setMinimumHeight(30)
            b.clicked.connect(lambda _=False, s=secs: self._preset(s))
            ch.addWidget(b, i // 3, i % 3)
        v1.addLayout(ch)
        self.stack.addWidget(p1)
        # repetir
        p2 = QWidget()
        v2 = QVBoxLayout(p2)
        v2.setContentsMargins(0, 4, 0, 0)
        v2.addWidget(label("A cada quantos minutos? (1 a 60)", "muted"))
        self.sp_int = QSpinBox()
        self.sp_int.setRange(1, 60)
        self.sp_int.setValue(10)
        self.sp_int.setSuffix(" min")
        v2.addWidget(self.sp_int)
        v2.addWidget(label("Quantas vezes? (1 a 10)", "muted"))
        self.sp_times = QSpinBox()
        self.sp_times.setRange(1, 10)
        self.sp_times.setValue(5)
        self.sp_times.setSuffix(" vezes")
        v2.addWidget(self.sp_times)
        v2.addStretch(1)
        self.stack.addWidget(p2)
        lv.addWidget(self.stack)
        lv.addWidget(label("Qual som tocar?", "muted"))
        self.combo = QComboBox()
        lv.addWidget(self.combo)
        self.err = label("", "error", wrap=True)
        lv.addWidget(self.err)
        go = QPushButton("Iniciar cronômetro")
        go.setObjectName("primary")
        go.clicked.connect(self._start)
        lv.addWidget(go)
        lv.addStretch(1)
        root.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)

        # ---- ativos
        right = QVBoxLayout()
        head = QHBoxLayout()
        self.title = label("Cronômetros ativos (0/5)", "h2")
        head.addWidget(self.title, 1)
        if is_host:
            ca = QPushButton("Cancelar todos")
            ca.setObjectName("danger")
            ca.clicked.connect(self.cancel_all_requested)
            head.addWidget(ca)
        right.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.hv = QVBoxLayout(self.holder)
        self.hv.setContentsMargins(0, 0, 8, 0)
        self.hv.setSpacing(8)
        self.hv.addStretch(1)
        self.scroll.setWidget(self.holder)
        right.addWidget(self.scroll, 1)
        self.none = label("Nenhum cronômetro ativo.", "muted")
        right.addWidget(self.none)
        root.addLayout(right, 1)

        for sp in (self.sp_h, self.sp_m, self.sp_s, self.sp_int, self.sp_times):
            sp.setToolTip("Digite o valor ou use a rodinha do mouse")
        self.clock = QTimer(self)
        self.clock.timeout.connect(lambda: [r.tick() for r in self.rows])
        self.clock.start(250)

    def _preset(self, secs):
        self.sp_h.setValue(secs // 3600)
        self.sp_m.setValue(secs % 3600 // 60)
        self.sp_s.setValue(secs % 60)

    def set_sounds(self, entries):
        cur = self.combo.currentData()
        self.names = {e["sid"]: e["name"] for e in entries}
        self.combo.clear()
        self.combo.addItem("Aleatório (qualquer som)", None)
        for e in entries:
            self.combo.addItem(e["name"], e["sid"])
        i = self.combo.findData(cur)
        self.combo.setCurrentIndex(max(0, i))

    def _start(self):
        sid = self.combo.currentData()
        if self.stack.currentIndex() == 0:
            secs = self.sp_h.value() * 3600 + self.sp_m.value() * 60 + self.sp_s.value()
            if secs <= 0:
                self.err.setText("Defina um tempo maior que zero.")
                return
            req = {"mode": "once", "seconds": secs, "times": 1, "sid": sid}
        else:
            req = {"mode": "repeat", "seconds": self.sp_int.value() * 60, "times": self.sp_times.value(), "sid": sid}
        self.err.setText("")
        self.create_requested.emit(req)

    def show_error(self, text):
        self.err.setText(text)

    def set_timers(self, timers):
        for r in self.rows:
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for t in timers:
            can = self.is_host or t["owner"] == self.my_id
            row = TimerRow(t, self.names.get(t["sid"], "aleatório"), can)
            row.cancel.connect(self.cancel_requested)
            self.hv.insertWidget(self.hv.count() - 1, row)
            self.rows.append(row)
        self.title.setText(f"Cronômetros ativos ({len(timers)}/{P.MAX_TIMERS})")
        self.none.setVisible(not timers)
