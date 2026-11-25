"""Card tabs added in phase 3: Files (shelf), Phone, Controls, Launcher and Web."""
import os
import subprocess

from PySide6.QtCore import QMimeData, QPoint, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDrag, QFont, QPainter
from PySide6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QHBoxLayout, QMenu, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .card import IconButton, elabel, label, scroll_area, slider
from .theme import G, font, icon_font, rgba, safe_tip

GL = {
    "phone": "", "controls": "", "apps": "", "globe": "", "star": "",
    "shot": "", "snip": "", "record": "", "stop": "", "wifi": "",
    "bt": "", "night": "", "mute": "", "micoff": "", "quiet": "",
    "add": "", "screens": "", "open": "",
}

_thumbs = {}


def file_pixmap(path, px=96):
    """Thumbnail for pictures/videos, the file-type icon otherwise (cached)."""
    key = (path.lower(), px)
    if key not in _thumbs:
        from .taskbar_core import shell_icon
        ext = os.path.splitext(path)[1].lower()
        thumb = ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".mp4", ".mov", ".mkv")
        # ask for the full 256 px: in-between sizes sometimes come back without transparency
        pm = shell_icon(path, 256, thumbnail=thumb)
        if pm.isNull():
            pm = shell_icon(path, 256, thumbnail=not thumb)
        if not pm.isNull() and pm.width() > px:
            pm = pm.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if len(_thumbs) > 200:
            _thumbs.clear()
        _thumbs[key] = pm
    return _thumbs[key]


def open_path(path):
    try:
        os.startfile(path)
    except OSError:
        pass


def show_in_folder(path):
    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])


def chip(ctx, glyph, text, cb, checkable=False):
    """A rounded button with an icon and a word."""
    S = ctx.S
    b = QPushButton(f"{glyph}  {text}" if text else glyph)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(font(S(12), QFont.DemiBold))
    b.setStyleSheet(
        f"QPushButton{{color:{ctx.fg.name()};background:{ctx.css(rgba(ctx.fg, 22))};border:none;"
        f"border-radius:{int(S(12))}px;padding:{int(S(7))}px {int(S(12))}px;font-family:'{icon_font(1).family()}',"
        f"'{font(1).family()}';}}"
        f"QPushButton:hover{{background:{ctx.css(rgba(ctx.fg, 36))};}}"
        f"QPushButton:checked{{background:{ctx.accent.name()};color:#000;}}")
    b.clicked.connect(cb)
    return b


def header(ctx, title, *widgets):
    h = QHBoxLayout()
    h.addWidget(label(ctx, title, 14, weight=QFont.DemiBold))
    h.addStretch()
    for w in widgets:
        h.addWidget(w)
    return h


# ---------------------------------------------------------------- files shelf
class FileTile(QFrame):
    def __init__(self, ctx, app, path):
        super().__init__()
        self.ctx, self.app, self.path = ctx, app, path
        S = ctx.S
        self.setObjectName("ft")
        self.setFixedSize(int(S(78)), int(S(84)))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(safe_tip(path))
        self.setStyleSheet(f"QFrame#ft{{background:transparent;border-radius:{int(S(12))}px;}}"
                           f"QFrame#ft:hover{{background:{ctx.css(rgba(ctx.fg, 22))};}}")
        v = QVBoxLayout(self)
        v.setContentsMargins(int(S(4)), int(S(6)), int(S(4)), int(S(4)))
        v.setSpacing(int(S(4)))
        self.icon = QWidget()
        self.icon.setFixedSize(int(S(44)), int(S(44)))
        self.icon.paintEvent = self._paint_icon
        v.addWidget(self.icon, 0, Qt.AlignHCenter)
        name = elabel(ctx, 10)
        name.setText(os.path.basename(path.rstrip("\\/")) or path)
        name.setAlignment(Qt.AlignHCenter)
        v.addWidget(name)
        self._press = None

    def _paint_icon(self, _):
        from .taskbar_core import draw_icon
        p = QPainter(self.icon)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        draw_icon(p, QRectF(self.icon.rect()), file_pixmap(self.path), self.devicePixelRatioF())

    def mousePressEvent(self, e):
        self._press = e.position().toPoint() if e.button() == Qt.LeftButton else None
        if e.button() == Qt.RightButton:
            m = QMenu(self)
            m.addAction("Open", lambda: open_path(self.path))
            m.addAction("Show in folder", lambda: show_in_folder(self.path))
            m.addAction("Copy path", lambda: __import__("PySide6.QtGui", fromlist=["QGuiApplication"])
                        .QGuiApplication.clipboard().setText(self.path))
            m.addSeparator()
            m.addAction("Remove from shelf", lambda: self.app.shelf.remove(self.path))
            m.popup(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._press is None or (e.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(self.path)])
        d = QDrag(self)
        d.setMimeData(md)
        pm = file_pixmap(self.path)
        if not pm.isNull():
            d.setPixmap(pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        d.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)

    def mouseReleaseEvent(self, e):
        if self._press is not None and e.button() == Qt.LeftButton:
            open_path(self.path)
        self._press = None


class ShelfPage(QWidget):
    COLS = 6

    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(4)))
        add = IconButton(ctx, GL["add"], 26, 12, "Add files")
        add.clicked.connect(self._add)
        clr = IconButton(ctx, G["clear"], 26, 12, "Clear shelf (files stay where they are)")
        clr.clicked.connect(app.shelf.clear)
        self.count = label(ctx, "", 11, ctx.dim)
        lay.addLayout(header(ctx, "Files", self.count, add, clr))
        self.sa, outer = scroll_area(ctx)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(int(S(2)))
        self.grid.setColumnStretch(self.COLS, 1)       # spare width goes to the right, not between tiles
        outer.addWidget(self.grid_host)
        outer.addStretch()
        lay.addWidget(self.sa, 1)
        app.shelf.changed.connect(lambda: self.isVisible() and self.refresh())

    def showEvent(self, e):
        self.app.shelf.prune()
        self.refresh()

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(None, "Add to the shelf")
        self.app.shelf.add(files)

    def refresh(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = self.app.shelf.items
        self.count.setText(f"{len(items)}" if items else "")
        if not items:
            self.grid.addWidget(label(self.ctx, "Drop files on the island to keep them here.\n"
                                                "Screenshots and recordings land here too.", 12, self.ctx.dim),
                                0, 0)
            return
        for n, it in enumerate(items):
            self.grid.addWidget(FileTile(self.ctx, self.app, it["path"]), n // self.COLS, n % self.COLS,
                                Qt.AlignLeft | Qt.AlignTop)


# ---------------------------------------------------------------- phone
class QRCode(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.m = []
        s = int(ctx.S(150))
        self.setFixedSize(s, s)

    def set_text(self, text):
        from .phone import qr_matrix
        self.m = qr_matrix(text) if text else []
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(self.rect()), self.ctx.S(14), self.ctx.S(14))
        if not self.m:
            return
        n = len(self.m)
        pad = self.ctx.S(10)
        cell = (self.width() - 2 * pad) / n
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setBrush(QColor("#000000"))
        for y, row in enumerate(self.m):
            for x, v in enumerate(row):
                if v:
                    p.drawRect(QRectF(pad + x * cell, pad + y * cell, cell + 0.6, cell + 0.6))


class PhonePage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(18)))
        self.qr = QRCode(ctx)
        lay.addWidget(self.qr, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(int(S(4)))
        col.addWidget(label(ctx, "Send to phone", 15, weight=QFont.DemiBold))
        t = label(ctx, "Scan with your phone's camera. Both need to be on the same Wi-Fi.\n"
                       "Send photos, files and text to this PC, and download what's on the shelf.", 11, ctx.dim)
        t.setWordWrap(True)
        col.addWidget(t)
        self.url = elabel(ctx, 10, ctx.dim)
        col.addWidget(self.url)
        self.status = elabel(ctx, 12, ctx.accent, QFont.DemiBold)
        col.addWidget(self.status)
        col.addStretch()
        row = QHBoxLayout()
        self.clip = chip(ctx, G["clip"], "Show my clipboard", self._toggle_clip, checkable=True)
        self.clip.setChecked(bool(app.settings["phone_clipboard"]))
        self.clip.setToolTip("Lets the phone page show your last 5 copied texts")
        row.addWidget(self.clip)
        row.addWidget(chip(ctx, GL["stop"], "Stop", self._stop))
        row.addStretch()
        col.addLayout(row)
        note = label(ctx, "Use it on Wi-Fi you trust (home, not a café): the transfer isn't encrypted. "
                          "If Windows asks, allow Notch on private networks.", 10, ctx.dim)
        note.setWordWrap(True)
        col.addWidget(note)
        lay.addLayout(col, 1)
        app.phone.received_file.connect(lambda p: self.status.setText("Received " + os.path.basename(p)))
        app.phone.received_text.connect(lambda t: self.status.setText("Received text"))

    def showEvent(self, e):
        try:
            url = self.app.phone.start()
        except OSError as ex:
            self.status.setText(f"Couldn't start: {ex}")
            return
        self.qr.set_text(url)
        self.url.setText(url.replace("/" + self.app.phone.token + "/", "/…"))

    def _toggle_clip(self):
        self.app.settings.set("phone_clipboard", self.clip.isChecked())

    def _stop(self):
        self.app.phone.stop()
        self.qr.set_text("")
        self.url.setText("Stopped. Open this tab again to share.")


# ---------------------------------------------------------------- controls
class Toggle(QPushButton):
    def __init__(self, ctx, glyph, text, on_click):
        super().__init__()
        self.ctx = ctx
        S = ctx.S
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(int(S(58)))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.glyph, self.text_ = glyph, text
        self.sub = ""
        self.clicked.connect(lambda: on_click(self.isChecked()))

    def paintEvent(self, _):
        S, ctx = self.ctx.S, self.ctx
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        bg = ctx.accent if on else rgba(ctx.fg, 40 if self.underMouse() else 22)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), S(14), S(14))
        fg = QColor("#000000") if on and ctx.accent.lightness() > 120 else ctx.fg
        p.setPen(fg)
        p.setFont(icon_font(S(16)))
        p.drawText(QRectF(S(12), 0, S(22), self.height()), Qt.AlignCenter, self.glyph)
        p.setFont(font(S(12), QFont.DemiBold))
        p.drawText(QRectF(S(42), S(10), self.width() - S(46), S(18)), Qt.AlignLeft | Qt.AlignVCenter, self.text_)
        p.setFont(font(S(10)))
        p.setPen(rgba(fg, 170))
        p.drawText(QRectF(S(42), S(28), self.width() - S(46), S(16)), Qt.AlignLeft | Qt.AlignVCenter,
                   self.sub or ("On" if on else "Off"))

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class ControlsPage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(8)))
        from . import tools
        self.tools = tools

        def slider_row(glyph, cb):
            h = QHBoxLayout()
            ic = label(ctx, glyph, 13)
            ic.setFont(icon_font(S(14)))
            ic.setFixedWidth(int(S(22)))
            h.addWidget(ic)
            s = slider(ctx)
            s.sliderMoved.connect(cb)
            s.valueChanged.connect(lambda v: s.isSliderDown() or cb(v))
            h.addWidget(s, 1)
            lay.addLayout(h)
            return s
        self.vol = slider_row(G["vol3"], lambda v: app.audio.set_level(v / 100))
        self.bri = slider_row(G["sun"], tools.set_brightness)
        grid = QGridLayout()
        grid.setSpacing(int(S(8)))
        self.t = {
            "wifi": Toggle(ctx, GL["wifi"], "Wi-Fi", lambda on: tools.set_radio("wifi", on, self._later)),
            "bt": Toggle(ctx, GL["bt"], "Bluetooth", lambda on: tools.set_radio("bluetooth", on, self._later)),
            "night": Toggle(ctx, GL["night"], "Night light", lambda on: (tools.set_night_light(on), self._sync())),
            "mute": Toggle(ctx, GL["mute"], "Mute", lambda on: (app.audio.toggle_mute(), self._sync())),
            "mic": Toggle(ctx, GL["micoff"], "Mic off", lambda on: (tools.set_mic_muted(on), self._sync())),
            "quiet": Toggle(ctx, GL["quiet"], "Quiet", lambda on: app.settings.set("quiet", on)),
        }
        self.t["quiet"].setToolTip("No notification popups on the island")
        for n, k in enumerate(("wifi", "bt", "night", "mute", "mic", "quiet")):
            grid.addWidget(self.t[k], n // 3, n % 3)
        lay.addLayout(grid)
        lay.addStretch()
        app.audio.volume_changed.connect(lambda *_: self.isVisible() and self._sync())

    def showEvent(self, e):
        self._sync()
        self.tools.radios_state(self._radios)

    def _later(self, _res=None):
        QTimer.singleShot(400, lambda: self.tools.radios_state(self._radios))

    def _radios(self, st):
        if not st or not self.isVisible():
            return
        for k, key in (("wifi", "wifi"), ("bt", "bluetooth")):
            v = st.get(key)
            self.t[k].setEnabled(v is not None)
            self.t[k].setChecked(bool(v))
            self.t[k].sub = "" if v is not None else "Not found"
            self.t[k].update()

    def _sync(self):
        a = self.app.audio
        if not self.vol.isSliderDown():
            self.vol.blockSignals(True)
            self.vol.setValue(int(round(a.level * 100)))
            self.vol.blockSignals(False)
        b = self.app.brightness
        self.bri.setEnabled(b.available)
        self.bri.setToolTip("" if b.available else "This screen doesn't let Windows set its brightness")
        if b.available and b.level is not None and not self.bri.isSliderDown():
            self.bri.blockSignals(True)
            self.bri.setValue(int(b.level))
            self.bri.blockSignals(False)
        nl = self.tools.night_light()
        self.t["night"].setEnabled(nl is not None)
        self.t["night"].setChecked(bool(nl))
        self.t["mute"].setChecked(a.muted)
        mm = self.tools.mic_muted()
        self.t["mic"].setEnabled(mm is not None)
        self.t["mic"].setChecked(bool(mm))
        self.t["quiet"].setChecked(bool(self.app.settings["quiet"]))
        for t in self.t.values():
            t.update()


# ---------------------------------------------------------------- launcher
class Slot(QFrame):
    def __init__(self, ctx, page, n):
        super().__init__()
        self.ctx, self.page, self.n = ctx, page, n
        S = ctx.S
        self.setObjectName("slot")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(int(S(64)))
        self.setStyleSheet(f"QFrame#slot{{background:{ctx.css(rgba(ctx.fg, 16))};border-radius:{int(S(14))}px;}}"
                           f"QFrame#slot:hover{{background:{ctx.css(rgba(ctx.fg, 30))};}}")

    def path(self):
        slots = self.page.app.settings["launcher_slots"] or []
        return slots[self.n] if self.n < len(slots) else None

    def paintEvent(self, e):
        super().paintEvent(e)
        S, ctx = self.ctx.S, self.ctx
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = self.path()
        if not path:
            p.setPen(ctx.dim)
            p.setFont(icon_font(S(14)))
            p.drawText(QRectF(0, 0, self.width(), self.height() - S(14)), Qt.AlignCenter, GL["add"])
            p.setFont(font(S(10)))
            p.drawText(QRectF(0, self.height() - S(22), self.width(), S(16)), Qt.AlignCenter, f"Slot {self.n + 1}")
            return
        from .taskbar_core import draw_icon
        draw_icon(p, QRectF(self.width() / 2 - S(14), S(8), S(28), S(28)), file_pixmap(path), self.devicePixelRatioF())
        p.setPen(ctx.fg)
        p.setFont(font(S(10)))
        if path.lower().endswith(".exe"):
            from .taskbar_core import app_name
            name = app_name(path)                  # "Microsoft Edge", not "msedge"
        else:
            name = os.path.splitext(os.path.basename(path.rstrip("\\/")))[0] or path
        from PySide6.QtGui import QFontMetrics
        p.drawText(QRectF(S(4), S(40), self.width() - S(8), S(16)), Qt.AlignCenter,
                   QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, int(self.width() - S(10))))

    def mouseReleaseEvent(self, e):
        if not self.rect().contains(e.position().toPoint()):
            return
        if e.button() == Qt.RightButton:
            m = QMenu(self)
            m.addAction("Choose app or file…", lambda: self.page.pick(self.n, folder=False))
            m.addAction("Choose folder…", lambda: self.page.pick(self.n, folder=True))
            if self.path():
                m.addAction("Clear slot", lambda: self.page.set_slot(self.n, None))
            m.popup(e.globalPosition().toPoint())
        elif self.path():
            self.page.app.island.set_expanded(False)
            open_path(self.path())
        else:
            self.page.pick(self.n)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.page.set_slot(self.n, urls[0])
            e.acceptProposedAction()


class LauncherPage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(10)))
        row = QHBoxLayout()
        row.setSpacing(int(S(6)))
        row.addWidget(chip(ctx, GL["shot"], "Screenshot", lambda: app.take_screenshot(False)))
        row.addWidget(chip(ctx, GL["screens"], "All screens", lambda: app.take_screenshot(True)))
        row.addWidget(chip(ctx, GL["snip"], "Snip", app.snip))
        self.rec = chip(ctx, GL["record"], "Record", self._record)
        row.addWidget(self.rec)
        self.mic = chip(ctx, G["mic"], "", lambda: app.settings.set("record_mic", self.mic.isChecked()),
                        checkable=True)
        self.mic.setToolTip("Record your microphone too")
        self.mic.setChecked(bool(app.settings["record_mic"]))
        row.addWidget(self.mic)
        row.addStretch()
        lay.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(int(S(8)))
        self.slots = [Slot(ctx, self, n) for n in range(6)]
        for n, s in enumerate(self.slots):
            grid.addWidget(s, n // 3, n % 3)
        lay.addLayout(grid)
        lay.addStretch()
        app.recorder.state_changed.connect(self._rec_state)

    def showEvent(self, e):
        self._rec_state(self.app.recorder.recording)

    def _record(self):
        r = self.app.recorder
        if r.recording:
            r.stop()
        else:
            self.app.start_recording()

    def _rec_state(self, on):
        self.rec.setText(f"{GL['stop']}  Stop" if on else f"{GL['record']}  Record")

    def pick(self, n, folder=False):
        self.app.island.set_expanded(False)
        if folder:
            path = QFileDialog.getExistingDirectory(None, f"Folder for slot {n + 1}")
        else:
            path, _ = QFileDialog.getOpenFileName(None, f"App or file for slot {n + 1}", "",
                                                  "Apps and files (*.*)")
        if path:
            self.set_slot(n, os.path.normpath(path))

    def set_slot(self, n, path):
        slots = list(self.app.settings["launcher_slots"] or [None] * 6)
        slots += [None] * (6 - len(slots))
        slots[n] = path
        self.app.settings.set("launcher_slots", slots)
        self.slots[n].update()


# ---------------------------------------------------------------- web
class WebPage(QWidget):
    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(8)))
        lay.addLayout(header(ctx, "Web"))
        t = label(ctx, "Opens a small browser under the island. Music keeps playing when you close it.", 11, ctx.dim)
        t.setWordWrap(True)
        lay.addWidget(t)
        self.grid = QGridLayout()
        self.grid.setSpacing(int(S(8)))
        lay.addLayout(self.grid)
        lay.addStretch()

    def showEvent(self, e):
        import importlib.util
        if importlib.util.find_spec("PySide6.QtWebEngineWidgets") is None:
            if not self.grid.count():
                self.grid.addWidget(label(self.ctx, "The mini browser isn't included in this copy of Notch.",
                                          12, self.ctx.dim), 0, 0)
            return
        from .webpanel import SITES
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        sites = SITES + [(p["name"], p["url"]) for p in (self.app.settings["web_pins"] or [])]
        for n, (name, url) in enumerate(sites[:8]):
            b = chip(self.ctx, GL["globe"], name, lambda _=False, u=url: self.app.open_web(u))
            self.grid.addWidget(b, n // 4, n % 4)


# ---------------------------------------------------------------- widgets
class WidgetsPage(QWidget):
    """The widgets you picked, in a grid. Right-click one to put it on the desktop."""
    COLS = 3

    def __init__(self, ctx, app):
        super().__init__()
        self.ctx, self.app = ctx, app
        S = ctx.S
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(S(4)))
        self.addb = IconButton(ctx, GL["add"], 26, 12, "Add a widget")
        self.addb.clicked.connect(self._add_menu)
        lay.addLayout(header(ctx, "Widgets", label(ctx, "right-click a widget for more", 10, ctx.dim), self.addb))
        self.sa, outer = scroll_area(ctx)
        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(int(S(8)))
        for c in range(self.COLS):
            self.grid.setColumnStretch(c, 1)
        outer.addWidget(self.host)
        outer.addStretch()
        lay.addWidget(self.sa, 1)
        self._built = None
        app.settings.changed.connect(lambda k: k == "widgets" and self.isVisible() and self.build())

    def showEvent(self, e):
        if self._built != list(self.app.settings["widgets"] or []):
            self.build()

    def build(self):
        from .widgets import WIDGETS
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        keys = [k for k in (self.app.settings["widgets"] or []) if k in WIDGETS]
        self._built = list(self.app.settings["widgets"] or [])
        row = col = 0
        for k in keys:
            cls = WIDGETS[k]
            span = min(cls.span, self.COLS)
            if col + span > self.COLS:
                row, col = row + 1, 0
            self.grid.addWidget(cls(self.ctx, self.app), row, col, cls.rows, span)
            col += span
            if col >= self.COLS:
                row, col = row + cls.rows, 0
        if not keys:
            self.grid.addWidget(label(self.ctx, "No widgets yet. Add some with +", 12, self.ctx.dim), 0, 0, 1, 3)

    def _add_menu(self):
        from .widgets import WIDGETS
        m = QMenu(self)
        have = set(self.app.settings["widgets"] or [])
        for k, cls in WIDGETS.items():
            a = m.addAction(cls.title, lambda k=k: self.app.widget_host.add(k))
            a.setEnabled(k not in have)
        m.popup(self.addb.mapToGlobal(self.addb.rect().bottomLeft()))
