"""Widgets on the desktop, the big day clock, the live video wallpaper, and the two background
checks widgets can ask for (website uptime and price alerts).

Desktop windows sit directly above Windows' desktop in the window stack (below every app, above
the icons), so they stay put through Win+D. They are re-pinned when the active window changes,
not on a timer. The video wallpaper lives inside the desktop's own wallpaper layer, behind the
icons, and pauses whenever a maximised or fullscreen window covers its screen.
"""
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import os
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from .theme import font, hide_from_capture, rgba

user32 = ctypes.windll.user32
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.SetParent.restype = wt.HWND
user32.SetParent.argtypes = [wt.HWND, wt.HWND]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
SWP_NOMOVE_NOSIZE_NOACT = 0x0001 | 0x0002 | 0x0010


def pin_above_desktop(hwnd):
    """Put a window just above the desktop: under every app window, over the icons."""
    prog = user32.FindWindowW("Progman", None)
    if not prog:
        return
    prev = user32.GetWindow(prog, 3)          # GW_HWNDPREV
    if prev and prev != hwnd:
        user32.SetWindowPos(hwnd, prev, 0, 0, 0, 0, SWP_NOMOVE_NOSIZE_NOACT)


class DesktopWindow(QWidget):
    """A frameless, see-through window on the desktop layer; drag it while unlocked."""

    def __init__(self, host, key):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint)
        self.host, self.key = host, key
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._drag = None

    def showEvent(self, e):
        hide_from_capture(self, self.host.app.settings["hide_from_capture"])
        pin_above_desktop(int(self.winId()))

    def place(self, default):
        pos = (self.host.app.settings["desktop_positions"] or {}).get(self.key)
        pt = QPoint(*pos) if pos else default
        # keep it on a screen that still exists
        if not any(s.geometry().contains(pt + QPoint(20, 20)) for s in QGuiApplication.screens()):
            pt = default
        self.move(pt)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.host.locked():
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.host.save_pos(self.key, self.pos())


class TileWindow(DesktopWindow):
    def __init__(self, host, key):
        super().__init__(host, key)
        from .card import Ctx
        from .widgets import WIDGETS
        ctx = Ctx(host.app.island.main)
        cls = WIDGETS[key]
        self.tile = cls(ctx, host.app, desktop=True)
        self.tile.setParent(self)
        w = int(ctx.S(168 * cls.span + 8 * (cls.span - 1)))
        self.tile.setFixedWidth(w)
        self.resize(w, self.tile.height())
        self.tile.move(0, 0)
        # dragging works anywhere except on a widget's own inputs
        self.tile.installEventFilter(self)

    def eventFilter(self, obj, e):
        t = e.type()
        if t == e.Type.MouseButtonPress and e.button() == Qt.LeftButton and not self.host.locked():
            self._drag = e.globalPosition().toPoint() - self.pos()
            self._moved = False
        elif t == e.Type.MouseMove and self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._moved = True
            return True
        elif t == e.Type.MouseButtonRelease and self._drag is not None:
            self._drag = None
            if getattr(self, "_moved", False):
                self.host.save_pos(self.key, self.pos())
                return True
        return False


class DayClock(DesktopWindow):
    """Big time and date on the desktop."""

    def __init__(self, host):
        super().__init__(host, "dayclock")
        self._tick = QTimer(self, singleShot=True, timeout=self._minute)
        self.relayout()

    def relayout(self):
        st = self.host.app.settings
        size = int(st["day_clock_size"])
        self.big = font(size, QFont.DemiBold, display=True)
        self.small = font(max(12, size // 5), QFont.Normal, display=True)
        self.resize(int(size * 4.2), int(size * 1.55))
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._minute()

    def hideEvent(self, e):
        self._tick.stop()

    def _minute(self):
        self.update()
        now = dt.datetime.now()
        self._tick.start(int((60 - now.second) * 1000 - now.microsecond / 1000 + 30))

    def paintEvent(self, _):
        st = self.host.app.settings
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        now = dt.datetime.now()
        t = now.strftime("%H:%M") if st["clock_24h"] else now.strftime("%I:%M").lstrip("0")
        col = QColor(st["day_clock_color"] or "#ffffff")
        size = int(st["day_clock_size"])
        r = QRectF(self.rect())
        shadow = QColor(0, 0, 0, 90)
        for dx, dy, c in ((2, 2, shadow), (0, 0, col)):
            p.setPen(c)
            p.setFont(self.big)
            p.drawText(r.adjusted(dx, dy, dx, dy - size * 0.45), Qt.AlignHCenter | Qt.AlignBottom, t)
            p.setFont(self.small)
            p.drawText(QRectF(dx, r.height() - size * 0.42 + dy, r.width(), size * 0.4), Qt.AlignHCenter | Qt.AlignTop,
                       now.strftime("%A, %d %B"))

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Unlock to move" if self.host.locked() else "Lock position", self.host.toggle_lock)
        m.addAction("Hide day clock", lambda: self.host.app.settings.set("day_clock", False))
        m.popup(e.globalPos())


# ---------------------------------------------------------------- live wallpaper
class LiveWallpaper(QObject):
    """A looping video behind the desktop icons on each chosen screen."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.views = []          # (screen, QVideoWidget, QMediaPlayer, QAudioOutput)
        self.paused = False
        # maximising a window doesn't always change the active window: look again every 5 s (~1 ms)
        self._watch = QTimer(self, interval=5000, timeout=self.check_cover)
        self._hidden_since, self._pos = {}, {}

    def start(self):
        self.stop()
        st = self.app.settings
        path = st["wallpaper_video"]
        if not path or not os.path.exists(path):
            return False
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        prog = user32.FindWindowW("Progman", None)
        res = ctypes.c_size_t()
        user32.SendMessageTimeoutW(prog, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))   # make the wallpaper layer
        layer = user32.FindWindowExW(prog, None, "WorkerW", None)
        if not layer:                         # older Windows: a top-level WorkerW behind the icons
            found = []

            @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            def cb(h, _):
                if user32.FindWindowExW(h, None, "SHELLDLL_DefView", None):
                    found.append(user32.FindWindowExW(None, h, "WorkerW", None))
                return True
            user32.EnumWindows(cb, 0)
            layer = found[0] if found else None
        if not layer:
            return False
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        primary = QGuiApplication.primaryScreen()
        screens = QGuiApplication.screens() if st["wallpaper_screens"] == "all" else [primary]
        for scr in screens:
            v = QVideoWidget()
            v.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
            v.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)
            v.setAttribute(Qt.WA_ShowWithoutActivating)
            pl = QMediaPlayer()
            audio = QAudioOutput()
            audio.setMuted(True)                # wallpapers are silent
            pl.setAudioOutput(audio)
            pl.setVideoOutput(v)
            pl.setLoops(QMediaPlayer.Infinite)
            pl.setSource(QUrl.fromLocalFile(path))
            g = scr.geometry()
            dpr = scr.devicePixelRatio()
            w, h = int(g.width() * dpr), int(g.height() * dpr)
            v.setGeometry(g)
            v.show()
            hwnd = int(v.winId())
            style = user32.GetWindowLongPtrW(hwnd, -16)
            user32.SetWindowLongPtrW(hwnd, -16, (style & ~0x80000000) | 0x40000000)   # child, not popup
            user32.SetParent(hwnd, layer)
            user32.SetWindowPos(hwnd, None, g.x() - vx, g.y() - vy, w, h, 0x0040 | 0x0010)
            pl.play()
            self.views.append((scr, v, pl, audio))
        self.paused = False
        QTimer.singleShot(1500, self.check_cover)      # once playback has really started
        self._watch.start()
        return True

    def stop(self):
        self._watch.stop()
        for _, v, pl, audio in self.views:
            pl.stop()
            pl.setVideoOutput(None)
            v.hide()
            v.deleteLater()
            pl.deleteLater()
            audio.deleteLater()
        had = bool(self.views)
        self.views = []
        if had:
            # ask the desktop to repaint its own wallpaper where the video was
            prog = user32.FindWindowW("Progman", None)
            user32.RedrawWindow(prog, None, None, 0x0001 | 0x0004 | 0x0080 | 0x0100)  # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW
        return had

    def check_cover(self):
        """Pause each video while a maximised or fullscreen window sits on its screen, or on battery."""
        if not self.views:
            return
        st = self.app.settings
        b = self.app.system.battery()
        on_battery = bool(b and not b.power_plugged and st["wallpaper_pause_battery"])
        covered = covered_screens() if st["wallpaper_pause_covered"] else set()
        for scr, v, pl, _ in self.views:
            g = scr.geometry()
            hide = on_battery or (g.x(), g.y()) in covered
            state = pl.playbackState()
            if hide:
                since = self._hidden_since.setdefault(id(pl), time.monotonic())
                if time.monotonic() - since > 120 and pl.source().isValid():
                    # covered for a while: unload the video so its decoder memory goes back to Windows
                    self._pos[id(pl)] = pl.position()
                    pl.stop()
                    pl.setSource(QUrl())
                elif state == pl.PlaybackState.PlayingState:
                    pl.pause()
            else:
                self._hidden_since.pop(id(pl), None)
                if not pl.source().isValid():
                    pl.setSource(QUrl.fromLocalFile(self.app.settings["wallpaper_video"]))
                    pos = self._pos.pop(id(pl), 0)
                    if pos:
                        pl.setPosition(pos)
                if state != pl.PlaybackState.PlayingState:
                    pl.play()


def covered_screens():
    """Top-left corners (pixels) of monitors mostly hidden behind a window (maximised, fullscreen,
    or covering 85% of the screen)."""
    out = set()

    class MI(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]
    from .taskbar_core import is_app_window

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not is_app_window(h) or user32.IsIconic(h):
            return True
        mi = MI()
        mi.cbSize = ctypes.sizeof(MI)
        user32.GetMonitorInfoW(user32.MonitorFromWindow(h, 2), ctypes.byref(mi))
        m = mi.rcMonitor
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        # visible area of the window on its monitor
        w = max(0, min(r.right, m.right) - max(r.left, m.left))
        h_ = max(0, min(r.bottom, m.bottom) - max(r.top, m.top))
        mon_area = max(1, (m.right - m.left) * (m.bottom - m.top))
        if user32.IsZoomed(h) or w * h_ >= 0.85 * mon_area:     # maximised, fullscreen or nearly so
            out.add((m.left, m.top))
        return True
    user32.EnumWindows(cb, 0)
    return out


# ---------------------------------------------------------------- background checks
class UptimeWatch(QObject):
    """Checks the Websites widget's addresses every 5 minutes (only if you added some)."""
    changed = Signal()
    _done = Signal(str, object)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.state = {}             # url -> (up, ms)
        self._last = 0.0
        self._done.connect(self._got)
        self._t = QTimer(self, interval=300_000, timeout=self.check)
        self.sync()
        app.settings.changed.connect(lambda k: k == "uptime_urls" and self.sync())

    def sync(self):
        if self.app.settings["uptime_urls"]:
            self._t.start()
            QTimer.singleShot(5000, self.check)
        else:
            self._t.stop()

    def check(self, force=False):
        if not force and time.monotonic() - self._last < 60:
            return
        self._last = time.monotonic()
        for u in (self.app.settings["uptime_urls"] or [])[:3]:
            threading.Thread(target=self._probe, args=(u,), daemon=True).start()

    def _probe(self, url):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": "NotchWidget/1.0 uptime"})
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read(256)
                ok = r.status < 500
        except urllib.error.HTTPError as e:
            ok = e.code < 500
        except Exception:
            ok = False
        self._done.emit(url, (ok, (time.perf_counter() - t0) * 1000))

    def _got(self, url, st):
        prev = self.state.get(url)
        self.state[url] = st
        self.changed.emit()
        if prev is not None and prev[0] != st[0]:
            host = urllib.parse.urlsplit(url).netloc or url
            self.app.island.show_hud({"kind": "message", "glyph": "",
                                      "color": "#30d158" if st[0] else "#ff453a",
                                      "sub": "Website back up" if st[0] else "Website down", "title": host,
                                      "w": 340, "h": 60}, 5)


import urllib.parse  # noqa: E402


class PriceAlerts(QObject):
    """Alerts like "BTC-USD>70000": checked every 5 minutes while any are set; each fires once."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.fired = set()
        self._t = QTimer(self, interval=300_000, timeout=self.check)
        self.sync()
        app.settings.changed.connect(lambda k: k == "price_alerts" and self.sync())

    def rules(self):
        out = []
        for a in self.app.settings["price_alerts"] or []:
            for op in (">", "<"):
                if op in a:
                    sym, val = a.split(op, 1)
                    try:
                        out.append((a, sym.strip(), op, float(val.replace(",", ""))))
                    except ValueError:
                        pass
                    break
        return out

    def sync(self):
        self.fired &= {r[0] for r in self.rules()}
        if self.rules():
            self._t.start()
            QTimer.singleShot(8000, self.check)
        else:
            self._t.stop()

    def check(self):
        from .widgets import fmt_num, quote
        for rule, sym, op, val in self.rules():
            if rule in self.fired:
                continue

            def got(d, rule=rule, sym=sym, op=op, val=val):
                if not d:
                    return
                price = d["price"]
                if (op == ">" and price > val) or (op == "<" and price < val):
                    self.fired.add(rule)
                    self.app.island.show_hud({"kind": "message", "glyph": "", "sub": "Price alert",
                                              "title": f"{sym} {fmt_num(price)}  ({op} {fmt_num(val)})",
                                              "w": 380, "h": 60, "urgent": True}, 8)
                    from .app import chime
                    chime()
            quote(sym, got)


# ---------------------------------------------------------------- the host
class WidgetHost(QObject):
    """Which widgets are on the island's Widgets tab and on the desktop; the day clock; the wallpaper."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.windows = {}          # key -> TileWindow
        self.clock = None
        self.wallpaper = LiveWallpaper(app)
        st = app.settings
        st.changed.connect(self._setting)
        app.system.foreground_changed.connect(self._foreground)
        self._cover = QTimer(self, singleShot=True, interval=400, timeout=self.wallpaper.check_cover)
        QTimer.singleShot(1200, self.apply)

    def locked(self):
        return bool(self.app.settings["desktop_locked"])

    def toggle_lock(self):
        self.app.settings.set("desktop_locked", not self.locked())

    # -- island tab
    def add(self, key):
        lst = list(self.app.settings["widgets"] or [])
        if key not in lst:
            self.app.settings.set("widgets", lst + [key])

    def remove(self, key):
        self.app.settings.set("widgets", [k for k in (self.app.settings["widgets"] or []) if k != key])

    # -- desktop
    def pin(self, key):
        lst = list(self.app.settings["desktop_widgets"] or [])
        if key not in lst:
            self.app.settings.set("desktop_widgets", lst + [key])

    def unpin(self, key):
        self.app.settings.set("desktop_widgets", [k for k in (self.app.settings["desktop_widgets"] or []) if k != key])

    def save_pos(self, key, pt):
        pos = dict(self.app.settings["desktop_positions"] or {})
        pos[key] = [pt.x(), pt.y()]
        self.app.settings.set("desktop_positions", pos)

    def apply(self):
        st = self.app.settings
        want = [k for k in (st["desktop_widgets"] or [])]
        from .widgets import WIDGETS
        for k in list(self.windows):
            if k not in want:
                self.windows.pop(k).deleteLater()
        g = QGuiApplication.primaryScreen().availableGeometry()
        for n, k in enumerate(want):
            if k in self.windows or k not in WIDGETS:
                continue
            w = TileWindow(self, k)
            w.place(QPoint(g.right() - w.width() - 24, g.top() + 80 + n * (w.height() + 12)))
            w.show()
            self.windows[k] = w
        if st["day_clock"]:
            if self.clock is None:
                self.clock = DayClock(self)
                self.clock.place(QPoint(g.center().x() - self.clock.width() // 2, g.top() + int(g.height() * 0.12)))
                self.clock.show()
            else:
                self.clock.relayout()
        elif self.clock is not None:
            self.clock.deleteLater()
            self.clock = None
        uses_usage = "screentime" in want or "screentime" in (st["widgets"] or [])
        self.app.usage.set_enabled(uses_usage)
        if st["wallpaper"] and st["wallpaper_video"]:
            self.wallpaper.start()
        else:
            self.wallpaper.stop()

    def _setting(self, key):
        if key in ("desktop_widgets", "day_clock", "widgets"):
            self.apply()
        elif key in ("day_clock_size", "day_clock_color", "clock_24h") and self.clock:
            self.clock.relayout()
        elif key in ("wallpaper", "wallpaper_video", "wallpaper_screens"):
            if self.app.settings["wallpaper"] and self.app.settings["wallpaper_video"]:
                self.wallpaper.start()
            else:
                self.wallpaper.stop()
        elif key in ("wallpaper_pause_covered", "wallpaper_pause_battery"):
            self.wallpaper.check_cover()
        elif key == "hide_from_capture":
            for w in list(self.windows.values()) + ([self.clock] if self.clock else []):
                hide_from_capture(w, self.app.settings[key])

    def _foreground(self):
        if self.windows or self.clock:
            # the shell reorders windows for a moment after a switch (Win+D raises the desktop):
            # pin now and once more after it has settled
            self._repin()
            QTimer.singleShot(200, self._repin)
            QTimer.singleShot(800, self._repin)
        if self.wallpaper.views:
            self._cover.start()

    def _repin(self):
        for w in list(self.windows.values()) + ([self.clock] if self.clock else []):
            pin_above_desktop(int(w.winId()))

    def shutdown(self):
        self.wallpaper.stop()
