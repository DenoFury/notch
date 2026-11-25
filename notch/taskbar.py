"""A notch-style taskbar along the bottom of the main screen.

Idle cost is ~zero: it repaints when an app opens/closes/activates, the song
changes, or the minute ticks over. The dock animation runs only while the
pointer is over the bar.
"""
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import math
import os

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QToolTip, QWidget

from .taskbar_core import WindowTracker, draw_icon
from .theme import G, font, hide_from_capture, icon_font, rgba, safe_tip, vol_glyph

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

BAR_H = 52          # visible bar height (logical px)
HEADROOM = 40       # transparent space above for magnified icons
SLOT = 46
ICON = 30
EAR = 12

VK_LWIN = 0x5B


def press_keys(*vks):
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, 2, 0)


# ---------------------------------------------------------------- appbar (reserve screen space)
from .winbar import APPBARDATA  # noqa: E402  (one shared definition for SHAppBarMessage)

ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS = 0, 1, 2, 3
ABE_BOTTOM = 3


class Item:
    """One thing on the bar: kind is start|app|media|timer|tray|status|clock|sep."""
    __slots__ = ("kind", "group", "w", "scale", "x", "rect")

    def __init__(self, kind, group=None, w=SLOT):
        self.kind, self.group, self.w = kind, group, w
        self.scale, self.x, self.rect = 1.0, 0.0, QRectF()


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


user32.MonitorFromWindow.restype = wt.HANDLE
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]


def monitor_of(hwnd):
    return user32.MonitorFromWindow(hwnd, 2)   # MONITOR_DEFAULTTONEAREST


class Taskbar(QWidget):
    def __init__(self, app, screen, tracker, main=True):
        # WindowDoesNotAcceptFocus: clicking the bar leaves your app focused (so "click to minimise"
        # works, and closing the calendar or quick settings returns to your app)
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.app = app
        self.st = app.settings
        self.scr = screen
        self.main = main
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)   # never inherit a start-up "busy" cursor

        self.tracker = tracker
        self.groups = []
        self.items = []
        self._hover_x = None
        self._hover_item = None
        self._press_item = None
        self._bar_w = 0.0          # animated width
        self._clock = QElapsedTimer()
        self._anim = QTimer(self, timeout=self._frame)
        self._anim.setTimerType(Qt.PreciseTimer)
        self._anim.setInterval(12)
        self._preview_timer = QTimer(self, singleShot=True, interval=420, timeout=self._show_preview)
        self.preview = None
        self._appbar = False
        self._minute = QTimer(self, singleShot=True, timeout=self._tick_minute)
        self.tray_apps = []
        self._tray_key = None
        self._flyout = None
        if main:
            # tray apps start and quit without telling anyone: look again after window changes and each minute
            self._tray_scan = QTimer(self, singleShot=True, interval=1500, timeout=self._scan_tray)
            self._tray_every = QTimer(self, interval=60_000, timeout=self._scan_tray)
            self._tray_every.start()
            self.tracker.changed.connect(self._tray_scan.start)
            self._scan_tray(rebuild=False)

        self.tracker.changed.connect(self.rebuild)
        self.tracker.active_changed.connect(self.update)
        self.tracker.explorer_restarted.connect(self._explorer_restarted)
        app.media.changed.connect(self.rebuild)
        app.media.art_changed.connect(self.update)
        app.timer.tick.connect(self._timer_tick)
        app.audio.volume_changed.connect(self._on_volume)
        self.st.changed.connect(self._on_setting)
        self._was_timer = False

        self.place()
        self.rebuild(snap=True)
        self._tick_minute()

    # ------------------------------------------------------------ geometry / appbar
    def S(self, v):
        return v * self.st["taskbar_scale"]

    def place(self):
        scr = self.scr.geometry()
        H = int(self.S(BAR_H + HEADROOM))
        self.setScreen(self.scr)
        self.setGeometry(scr.x(), scr.y() + scr.height() - H, scr.width(), H)

    def monitor(self):
        return monitor_of(int(self.winId()))

    def register_appbar(self):
        """Tell Windows to keep maximised windows above the bar."""
        if self._appbar:
            return
        hwnd = int(self.winId())
        abd = APPBARDATA(ctypes.sizeof(APPBARDATA), hwnd)
        shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(abd))
        self._appbar = True
        self._appbar_pos()

    def _appbar_pos(self):
        if not self._appbar:
            return
        dpr = self.scr.devicePixelRatio()
        # the monitor's real pixel rectangle (Qt's coordinates are scaled per screen)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(self.monitor(), ctypes.byref(mi))
        m = mi.rcMonitor
        left, right, bottom = m.left, m.right, m.bottom
        abd = APPBARDATA(ctypes.sizeof(APPBARDATA), int(self.winId()))
        abd.uEdge = ABE_BOTTOM
        abd.rc = wt.RECT(left, bottom - int(self.S(BAR_H) * dpr), right, bottom)
        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(abd))
        # always sit on the screen's bottom edge, not stacked above Windows' (hidden) taskbar
        abd.rc.bottom = bottom
        abd.rc.top = bottom - int(self.S(BAR_H) * dpr)
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(abd))

    def unregister_appbar(self):
        if self._appbar:
            abd = APPBARDATA(ctypes.sizeof(APPBARDATA), int(self.winId()))
            shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(abd))
            self._appbar = False

    def _explorer_restarted(self):
        self._appbar = False
        QTimer.singleShot(1500, self.register_appbar)
        if self.main:
            QTimer.singleShot(1500, self.app.taskbar_hider.apply)

    def showEvent(self, e):
        self.register_appbar()
        hide_from_capture(self, self.st["hide_from_capture"])

    def closeEvent(self, e):
        self.unregister_appbar()

    # ------------------------------------------------------------ content
    def my_groups(self):
        """Pinned apps always; open apps only if they have a window on this screen
        (when 'each screen's own apps' is on)."""
        if not self.st["taskbar_own_apps"] or len(self.app.taskbars) < 2:
            return self.tracker.groups
        from .taskbar_core import AppGroup
        mon = self.monitor()
        out = []
        for g in self.tracker.groups:
            wins = [(h, t) for h, t in g.windows if monitor_of(h) == mon]
            if not wins and not g.pinned:
                continue
            ng = AppGroup(g.key, g.path, g.name, g.pinned)
            ng.exe, ng.windows = g.exe, wins
            if not wins and g.windows:
                ng.hwnd_hint = g.windows[0][0]   # open on the other screen: bring that one forward
            out.append(ng)
        return out

    def rebuild(self, snap=False):
        items = [Item("start")]
        self.groups = self.my_groups()
        for g in self.groups:
            items.append(Item("app", g))
        items.append(Item("sep", w=self.S(14)))
        m = self.app.media
        if m.available:
            items.append(Item("media", w=self.S(190)))
        if self.app.timer.running:
            items.append(Item("timer", w=self.S(74)))
        if self.main:
            for ta in self.tray_apps:
                if ta.promoted:
                    items.append(Item("trayapp", ta, w=self.S(30)))
            items.append(Item("tray", w=self.S(30)))
        items.append(Item("status", w=self.S(96)))
        items.append(Item("clock", w=self.S(78)))
        items.append(Item("desktop", w=self.S(18)))
        for it in items:
            if it.kind in ("start", "app"):
                it.w = self.S(SLOT)
        # keep animated scales of apps that stay
        old = {i.group.key if i.group else i.kind: i.scale for i in self.items}
        for it in items:
            it.scale = old.get(it.group.key if it.group else it.kind, 1.0)
        self.items = items
        if snap:
            self._bar_w = self._target_width()
        self._start_anim()

    def _scan_tray(self, rebuild=True):
        from .tray_apps import running_tray_apps
        apps = running_tray_apps()
        key = tuple((a.key, a.promoted) for a in apps)
        self.tray_apps = apps
        if key != self._tray_key:
            self._tray_key = key
            if rebuild:
                self.rebuild()

    def open_tray_app(self, ta):
        from .tray_apps import open_app
        if not open_app(ta, self.tracker):
            self.app.taskbar_hider.peek()     # nothing we can open: show Windows' own tray icon

    def tray_menu(self, ta, pos):
        from .tray_apps import can_open
        m = QMenu(self)
        m.addAction(ta.name).setEnabled(False)
        m.addSeparator()
        if can_open(ta):
            m.addAction("Open", lambda: self.open_tray_app(ta))
        m.addAction("Its menu in Windows' tray…", self.app.taskbar_hider.peek)
        m.popup(pos)

    def _open_flyout(self, it):
        from .taskbar_tray import TrayFlyout
        self._scan_tray()
        if not self.tray_apps:
            return self.app.taskbar_hider.peek()
        self._flyout = TrayFlyout(self)
        anchor = self.mapToGlobal(QPointF(it.rect.center().x(), self.bar_rect().top()).toPoint())
        self._flyout.show_for(self.tray_apps, anchor)

    def _target_width(self):
        return sum(i.w * (i.scale if i.kind in ("start", "app") else 1) for i in self.items) + self.S(20)

    def _on_volume(self, *_):
        self.update()

    def _timer_tick(self):
        if self.app.timer.running != self._was_timer:
            self._was_timer = self.app.timer.running
            self.rebuild()
        else:
            self.update()

    def _tick_minute(self):
        self.update()
        now = dt.datetime.now()
        self._minute.start(int((60 - now.second) * 1000 - now.microsecond / 1000 + 50))

    def _on_setting(self, key):
        if key == "hide_from_capture":
            return hide_from_capture(self, self.st[key])
        if key in ("taskbar_scale",):
            self.place()
            self.unregister_appbar()
            self.register_appbar()
            self.rebuild(snap=True)
        elif key.startswith("taskbar") or key in ("surface", "accent", "clock_24h"):
            self.rebuild()

    # ------------------------------------------------------------ animation (dock effect + width)
    def _start_anim(self):
        if not self._anim.isActive():
            self._clock.start()
            self._anim.start()

    def _frame(self):
        dt_s = min(0.05, self._clock.restart() / 1000.0)
        k = 1 - math.exp(-dt_s * 18)   # smoothing
        moving = False
        dock = self.st["taskbar_dock"] and self._hover_x is not None
        mag = float(self.st["taskbar_dock_size"])
        for it in self.items:
            if it.kind not in ("start", "app"):
                continue
            target = 1.0
            if dock:
                cx = it.x + it.w * it.scale / 2
                d = (self._hover_x - cx) / self.S(SLOT * 1.35)
                target = 1 + (mag - 1) * math.exp(-d * d)
            if abs(target - it.scale) > 0.002:
                it.scale += (target - it.scale) * k
                moving = True
            else:
                it.scale = target
        tw = self._target_width()
        if abs(tw - self._bar_w) > 0.3:
            self._bar_w += (tw - self._bar_w) * k
            moving = True
        else:
            self._bar_w = tw
        self.update()
        if not moving:
            self._anim.stop()

    # ------------------------------------------------------------ layout helpers
    def bar_rect(self):
        h = self.S(BAR_H)
        return QRectF((self.width() - self._bar_w) / 2, self.height() - h, self._bar_w, h)

    def _layout(self):
        r = self.bar_rect()
        x = r.left() + self.S(10)
        for it in self.items:
            w = it.w * (it.scale if it.kind in ("start", "app") else 1)
            it.x = x
            it.rect = QRectF(x, r.top(), w, r.height())
            x += w

    def item_at(self, p):
        for it in self.items:
            if it.rect.contains(p) and it.kind != "sep":
                return it
        return None

    # ------------------------------------------------------------ input
    def mouseMoveEvent(self, e):
        p = e.position()
        if not self.bar_rect().adjusted(0, -self.S(HEADROOM), 0, 0).contains(p):
            return self.leaveEvent(None)
        self._hover_x = p.x()
        it = self.item_at(QPointF(p.x(), self.bar_rect().center().y()))
        if it is not self._hover_item:
            self._hover_item = it
            QToolTip.hideText()
            if self.preview and self.preview.isVisible() and (not it or it.kind != "app"):
                self.preview.schedule_hide()
            if it and it.kind == "app" and self.st["taskbar_previews"]:
                self._preview_timer.start()
            else:
                self._preview_timer.stop()
            if it and it.kind == "desktop":
                QToolTip.showText(QCursor.pos(), "Show desktop", self)
            elif it and it.kind == "trayapp":
                QToolTip.showText(QCursor.pos(), safe_tip(it.group.name), self)
            elif it and it.kind == "tray":
                QToolTip.showText(QCursor.pos(), "Tray apps", self)
        self._start_anim()

    def leaveEvent(self, e):
        self._hover_x = None
        self._hover_item = None
        self._preview_timer.stop()
        if self.preview:
            self.preview.schedule_hide()
        self._start_anim()

    def mousePressEvent(self, e):
        self._press_item = self.item_at(QPointF(e.position().x(), self.bar_rect().center().y()))

    def mouseReleaseEvent(self, e):
        it = self.item_at(QPointF(e.position().x(), self.bar_rect().center().y()))
        if it is None or it is not self._press_item:
            return
        btn = e.button()
        if btn == Qt.RightButton:
            return self._menu(it, e.globalPosition().toPoint())
        if btn == Qt.MiddleButton and it.kind == "app":
            return self.tracker.launch(it.group.path)
        if btn != Qt.LeftButton:
            return
        k = it.kind
        if k == "start":
            press_keys(VK_LWIN)
        elif k == "app":
            self._click_app(it, from_mouse=True)
        elif k == "media":
            self.app.media.toggle()
        elif k == "timer":
            self.app.timer.toggle()
        elif k == "tray":
            self._open_flyout(it)
        elif k == "trayapp":
            self.open_tray_app(it.group)
        elif k == "status":
            press_keys(VK_LWIN, 0x41)     # Win+A: quick settings
        elif k == "clock":
            press_keys(VK_LWIN, 0x4E)     # Win+N: calendar & notifications
        elif k == "desktop":
            press_keys(VK_LWIN, 0x44)     # Win+D: show desktop / bring windows back

    def _click_app(self, it, from_mouse=False):
        if self.preview:
            self.preview.hide_now()
        if from_mouse and len(it.group.windows) > 1 and self.st["taskbar_previews"]:
            self._hover_item = it
            self._show_preview(force=True)
        elif not it.group.windows and it.group.hwnd_hint:
            self.tracker.activate(it.group.hwnd_hint)
        else:
            self.tracker.click(it.group)

    def open_index(self, n):
        """Win+n: the n-th app on the bar (start it, bring it forward, or minimise it)."""
        apps = [i for i in self.items if i.kind == "app"]
        if 1 <= n <= len(apps):
            self._click_app(apps[n - 1])

    def wheelEvent(self, e):
        it = self.item_at(QPointF(e.position().x(), self.bar_rect().center().y()))
        if it and it.kind in ("status", "media"):
            a = self.app.audio
            a.set_level(a.level + (0.04 if e.angleDelta().y() > 0 else -0.04))

    def _menu(self, it, pos):
        if it.kind == "trayapp":
            return self.tray_menu(it.group, pos)
        m = QMenu(self)
        if it.kind == "app":
            g = it.group
            m.addAction(g.name).setEnabled(False)
            m.addSeparator()
            if g.windows:
                m.addAction("New window", lambda: self.tracker.launch(g.path))
            if g.pinned:
                m.addAction("Unpin from taskbar", lambda: self.tracker.unpin(g))
            else:
                m.addAction("Pin to taskbar", lambda: self.tracker.pin(g))
            if g.windows:
                m.addSeparator()
                label = "Close window" if len(g.windows) == 1 else f"Close all {len(g.windows)} windows"
                m.addAction(label, lambda: [self.tracker.close(h) for h, _ in g.windows])
        else:
            m.addAction("Taskbar settings", self.app.open_settings)
            m.addAction("Task Manager", lambda: press_keys(0x11, 0x10, 0x1B))   # Ctrl+Shift+Esc
        m.popup(pos)

    def _show_preview(self, force=False):
        it = self._hover_item
        if it is None or it.kind != "app" or not it.group.windows:
            if it and it.kind == "app":
                QToolTip.showText(QCursor.pos(), safe_tip(it.group.name), self)
            return
        from .taskbar_preview import PreviewPopup
        if self.preview is None:
            self.preview = PreviewPopup(self)
        anchor = self.mapToGlobal(QPointF(it.rect.center().x(), self.bar_rect().top()).toPoint())
        self.preview.show_for(it.group, anchor)

    # ------------------------------------------------------------ painting
    def surface(self):
        fill, fg, dim, line = self.st.surface()
        return QColor(*fill), QColor(fg), QColor(dim), QColor(line)

    def bar_path(self, r):
        rad, ear = self.S(20), self.S(EAR)
        x0, x1, y0, y1 = r.left(), r.right(), r.top(), r.bottom()
        p = QPainterPath()
        p.moveTo(x0 - ear, y1)
        p.quadTo(x0, y1, x0, y1 - ear)
        p.lineTo(x0, y0 + rad)
        p.quadTo(x0, y0, x0 + rad, y0)
        p.lineTo(x1 - rad, y0)
        p.quadTo(x1, y0, x1, y0 + rad)
        p.lineTo(x1, y1 - ear)
        p.quadTo(x1, y1, x1 + ear, y1)
        p.closeSubpath()
        return p

    def paintEvent(self, _):
        self._layout()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        bg, fg, dim, line = self.surface()
        accent = QColor(self.st.accent())
        r = self.bar_rect()
        path = self.bar_path(r)
        p.fillPath(path, bg)
        if bg.alpha() < 255 or bg.lightness() > 200:
            p.setPen(QPen(rgba(line, 200), 1))
            p.drawPath(path)
        S = self.S
        cy = r.center().y()
        dpr = self.devicePixelRatioF()
        active = self.tracker.active
        for it in self.items:
            k = it.kind
            if k in ("start", "app"):
                size = S(ICON) * it.scale
                cx = it.rect.center().x()
                # centred on the bar at rest; when magnified the bottom stays put and it grows upwards
                top = cy + S(ICON) / 2 - size
                ir = QRectF(cx - size / 2, top, size, size)
                if it is self._hover_item and not self.st["taskbar_dock"]:
                    p.setPen(Qt.NoPen)
                    p.setBrush(rgba(fg, 24))
                    p.drawRoundedRect(QRectF(it.rect.left() + S(3), r.top() + S(5), it.rect.width() - S(6),
                                             r.height() - S(10)), S(10), S(10))
                if k == "start":
                    self._draw_start(p, ir, fg, accent)
                else:
                    pm = it.group.icon()
                    if not pm.isNull():
                        draw_icon(p, ir, pm, dpr)
                    else:
                        p.setPen(fg)
                        p.setFont(font(S(12), QFont.DemiBold))
                        p.drawText(ir, Qt.AlignCenter, it.group.name[:1])
                    n = len(it.group.windows)
                    if n:
                        is_active = any(h == active for h, _ in it.group.windows)
                        w = S(14) if is_active else S(5)
                        p.setPen(Qt.NoPen)
                        p.setBrush(accent if is_active else rgba(fg, 150))
                        p.drawRoundedRect(QRectF(cx - w / 2, r.bottom() - S(7), w, S(3)), S(1.5), S(1.5))
            elif k == "trayapp":
                if it is self._hover_item:
                    p.setPen(Qt.NoPen)
                    p.setBrush(rgba(fg, 26))
                    p.drawRoundedRect(QRectF(it.rect.left() + S(2), r.top() + S(10), it.rect.width() - S(4),
                                             r.height() - S(20)), S(7), S(7))
                pm = it.group.pixmap()
                ic = S(18)
                if not pm.isNull():
                    draw_icon(p, QRectF(it.rect.center().x() - ic / 2, cy - ic / 2, ic, ic), pm, dpr)
            elif k == "sep":
                p.setPen(QPen(rgba(fg, 40), 1))
                x = it.rect.center().x()
                p.drawLine(QPointF(x, cy - S(12)), QPointF(x, cy + S(12)))
            elif k == "media":
                self._draw_media(p, it.rect, cy, fg, dim, accent)
            elif k == "timer":
                p.setFont(icon_font(S(13)))
                p.setPen(accent)
                p.drawText(QRectF(it.rect.left(), r.top(), S(22), r.height()), Qt.AlignCenter, G["timer"])
                p.setFont(font(S(13), QFont.DemiBold))
                p.setPen(fg)
                p.drawText(QRectF(it.rect.left() + S(22), r.top(), it.rect.width() - S(22), r.height()),
                           Qt.AlignCenter, self.app.timer.text())
            elif k == "tray":
                p.setFont(icon_font(S(11)))
                p.setPen(dim if it is not self._hover_item else fg)
                p.drawText(it.rect, Qt.AlignCenter, "")   # chevron up
            elif k == "status":
                self._draw_status(p, it.rect, fg, dim, accent)
            elif k == "desktop":
                hov = it is self._hover_item
                p.setPen(Qt.NoPen)
                if hov:
                    p.setBrush(rgba(fg, 30))
                    p.drawRoundedRect(QRectF(it.rect.left() + S(2), r.top() + S(8), it.rect.width() - S(4),
                                             r.height() - S(16)), S(5), S(5))
                p.setPen(QPen(rgba(fg, 140 if hov else 60), S(1.5)))
                x = it.rect.center().x()
                p.drawLine(QPointF(x, cy - S(10)), QPointF(x, cy + S(10)))
            elif k == "clock":
                now = dt.datetime.now()
                t = now.strftime("%H:%M") if self.st["clock_24h"] else now.strftime("%I:%M %p").lstrip("0")
                p.setPen(fg)
                p.setFont(font(S(13), QFont.DemiBold))
                p.drawText(QRectF(it.rect.left(), cy - S(17), it.rect.width(), S(18)), Qt.AlignCenter, t)
                p.setPen(dim)
                p.setFont(font(S(11)))
                p.drawText(QRectF(it.rect.left(), cy + S(1), it.rect.width(), S(16)), Qt.AlignCenter,
                           now.strftime("%d/%m/%Y"))

    def _draw_start(self, p, r, fg, accent):
        g = r.width() * 0.09
        s = (r.width() * 0.72 - g) / 2
        x0 = r.center().x() - s - g / 2
        y0 = r.center().y() - s - g / 2
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        for i in range(2):
            for j in range(2):
                p.drawRoundedRect(QRectF(x0 + i * (s + g), y0 + j * (s + g), s, s), s * 0.18, s * 0.18)

    def _draw_media(self, p, r, cy, fg, dim, accent):
        S, m = self.S, self.app.media
        a = S(30)
        art = QRectF(r.left() + S(4), cy - a / 2, a, a)
        if not m.art.isNull():
            clip = QPainterPath()
            clip.addRoundedRect(art, S(7), S(7))
            p.save()
            p.setClipPath(clip)
            p.drawImage(art, m.art_small(int(a * self.devicePixelRatioF())))
            p.restore()
        else:
            p.setPen(accent)
            p.setFont(icon_font(S(14)))
            p.drawText(art, Qt.AlignCenter, G["music"])
        from .island import elided
        left = art.right() + S(8)
        width = r.right() - left - S(26)
        k1, k2 = (S(12), QFont.DemiBold), (S(11), QFont.Normal)
        p.setPen(fg)
        p.setFont(font(*k1))
        p.drawText(QRectF(left, cy - S(16), width, S(16)), Qt.AlignLeft | Qt.AlignVCenter,
                   elided(m.title, k1, int(width)))
        p.setPen(dim)
        p.setFont(font(*k2))
        p.drawText(QRectF(left, cy, width, S(15)), Qt.AlignLeft | Qt.AlignVCenter,
                   elided(m.artist or m.app, k2, int(width)))
        p.setPen(fg)
        p.setFont(icon_font(S(13)))
        p.drawText(QRectF(r.right() - S(26), r.top(), S(24), r.height()), Qt.AlignCenter,
                   G["pause"] if m.playing else G["play"])

    def _draw_status(self, p, r, fg, dim, accent):
        S = self.S
        a = self.app.audio
        glyphs = ["", vol_glyph(a.level, a.muted)]   # wifi, volume
        p.setFont(icon_font(S(14)))
        p.setPen(fg)
        x = r.left() + S(6)
        for gch in glyphs:
            p.drawText(QRectF(x, r.top(), S(22), r.height()), Qt.AlignCenter, gch)
            x += S(24)
        b = self.app.system.battery()
        if b:
            bw, bh = S(20), S(10)
            body = QRectF(x + S(2), r.center().y() - bh / 2, bw, bh)
            p.setPen(QPen(rgba(fg, 200), S(1.2)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(body, S(2.5), S(2.5))
            p.fillRect(QRectF(body.right() + S(1), r.center().y() - S(2), S(1.6), S(4)), rgba(fg, 200))
            inner = body.adjusted(S(2), S(2), -S(2), -S(2))
            inner.setWidth(inner.width() * b.percent / 100)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#30d158") if b.power_plugged else (QColor("#ff453a") if b.percent <= 20 else fg))
            p.drawRect(inner)
