"""Live window previews above the taskbar, drawn by Windows' DWM thumbnail service."""
import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .theme import font, icon_font, rgba

dwm = ctypes.windll.dwmapi
user32 = ctypes.windll.user32


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wt.DWORD), ("rcDestination", wt.RECT), ("rcSource", wt.RECT),
                ("opacity", ctypes.c_ubyte), ("fVisible", wt.BOOL), ("fSourceClientAreaOnly", wt.BOOL)]


dwm.DwmRegisterThumbnail.argtypes = [wt.HWND, wt.HWND, ctypes.POINTER(ctypes.c_void_p)]
dwm.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwm.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwm.DwmQueryThumbnailSourceSize.argtypes = [ctypes.c_void_p, ctypes.POINTER(SIZE)]
dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

TILE_W, TILE_H, TITLE_H, PAD = 236, 150, 30, 10
DWM_TNP = 0x1 | 0x4 | 0x8 | 0x10   # destination | opacity | visible | client area only


class PreviewPopup(QWidget):
    def __init__(self, taskbar):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.taskbar = taskbar
        self.app = taskbar.app
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.group = None
        self.tiles = []          # [(hwnd, title, QRect tile, QRect close)]
        self._thumbs = []
        self._hover = None
        self._hide_timer = QTimer(self, singleShot=True, interval=280, timeout=self._maybe_hide)
        self.winId()
        pref = ctypes.c_int(2)  # rounded corners
        dwm.DwmSetWindowAttribute(int(self.winId()), 33, ctypes.byref(pref), 4)

    # ------------------------------------------------------------ show / hide
    def show_for(self, group, anchor: QPoint):
        self._hide_timer.stop()
        from .theme import hide_from_capture
        hide_from_capture(self, self.app.settings["hide_from_capture"])
        self.group = group
        wins = group.windows[:6]
        n = len(wins)
        S = self.taskbar.S
        W = int(n * S(TILE_W) + (n + 1) * S(PAD))
        H = int(S(TITLE_H) + S(TILE_H) + 2 * S(PAD))
        scr = self.screen().geometry() if self.screen() else self.taskbar.screen().geometry()
        x = max(scr.left() + 8, min(anchor.x() - W // 2, scr.right() - W - 8))
        y = anchor.y() - H - int(S(10))
        self.setGeometry(x, y, W, H)
        self.tiles = []
        for i, (h, title) in enumerate(wins):
            tx = int(S(PAD) + i * (S(TILE_W) + S(PAD)))
            tile = QRect(tx, int(S(PAD)), int(S(TILE_W)), int(S(TITLE_H) + S(TILE_H)))
            close = QRect(tile.right() - int(S(26)), tile.top() + int(S(4)), int(S(22)), int(S(22)))
            self.tiles.append((h, title, tile, close))
        self.show()
        self.raise_()
        self._register()
        self.update()

    def _register(self):
        self._unregister()
        dpr = self.devicePixelRatioF()
        S = self.taskbar.S
        dest = int(self.winId())
        for h, _, tile, _ in self.tiles:
            th = ctypes.c_void_p()
            if dwm.DwmRegisterThumbnail(dest, h, ctypes.byref(th)) != 0:
                continue
            self._thumbs.append(th)
            src = SIZE()
            dwm.DwmQueryThumbnailSourceSize(th, ctypes.byref(src))
            area = QRectF(tile.left() + S(6), tile.top() + S(TITLE_H), tile.width() - S(12), S(TILE_H) - S(8))
            if src.cx > 0 and src.cy > 0:
                scale = min(area.width() / src.cx * dpr, area.height() / src.cy * dpr, 1.0 * dpr) / dpr
                w, hgt = src.cx * scale, src.cy * scale
            else:
                w, hgt = area.width(), area.height()
            r = QRectF(area.center().x() - w / 2, area.center().y() - hgt / 2, w, hgt)
            props = DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = DWM_TNP
            props.rcDestination = wt.RECT(int(r.left() * dpr), int(r.top() * dpr),
                                          int(r.right() * dpr), int(r.bottom() * dpr))
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = False
            dwm.DwmUpdateThumbnailProperties(th, ctypes.byref(props))

    def _unregister(self):
        for th in self._thumbs:
            dwm.DwmUnregisterThumbnail(th)
        self._thumbs = []

    def schedule_hide(self):
        if self.isVisible():
            self._hide_timer.start()

    def _maybe_hide(self):
        if self.geometry().adjusted(-4, -4, 4, 16).contains(QCursor.pos()):
            self._hide_timer.start()
            return
        hov = self.taskbar._hover_item
        if hov is not None and hov.kind == "app" and hov.group is self.group:
            self._hide_timer.start()
            return
        self.hide_now()

    def hide_now(self):
        self._hide_timer.stop()
        self._unregister()
        self.hide()

    def leaveEvent(self, e):
        self._hover = None
        self.update()
        self.schedule_hide()

    def enterEvent(self, e):
        self._hide_timer.stop()

    # ------------------------------------------------------------ input
    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        hov = None
        for i, (_, _, tile, close) in enumerate(self.tiles):
            if tile.contains(p):
                hov = (i, close.contains(p))
        if hov != self._hover:
            self._hover = hov
            self.update()

    def mouseReleaseEvent(self, e):
        p = e.position().toPoint()
        tr = self.taskbar.tracker
        for h, _, tile, close in self.tiles:
            if close.contains(p):
                tr.close(h)
                QTimer.singleShot(250, self._refresh_after_close)
                return
            if tile.contains(p):
                if e.button() == Qt.MiddleButton:
                    tr.close(h)
                    QTimer.singleShot(250, self._refresh_after_close)
                    return
                self.hide_now()
                tr.activate(h)
                return

    def _refresh_after_close(self):
        self.taskbar.tracker.refresh()
        g = next((x for x in self.taskbar.tracker.groups if x.key == (self.group.key if self.group else None)), None)
        if g and g.windows:
            self.show_for(g, QPoint(self.geometry().center().x(), self.geometry().bottom() + int(self.taskbar.S(10))))
        else:
            self.hide_now()

    # ------------------------------------------------------------ painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fill, fg, dim, line = self.app.settings.surface()
        bg = QColor(*fill)
        bg.setAlpha(255)
        if bg.lightness() < 30:
            bg = QColor(28, 28, 32)
        fg, dim = QColor(fg), QColor(dim)
        p.fillRect(self.rect(), bg)
        S = self.taskbar.S
        for i, (h, title, tile, close) in enumerate(self.tiles):
            hov = self._hover and self._hover[0] == i
            if hov:
                p.setPen(Qt.NoPen)
                p.setBrush(rgba(fg, 22))
                p.drawRoundedRect(QRectF(tile), S(10), S(10))
            icon = self.group.icon() if self.group else None
            if icon is not None and not icon.isNull():
                from .taskbar_core import draw_icon
                draw_icon(p, QRectF(tile.left() + S(8), tile.top() + S(7), S(16), S(16)), icon,
                          self.devicePixelRatioF())
            p.setPen(fg)
            f = font(S(12), QFont.DemiBold)
            p.setFont(f)
            tw = tile.width() - int(S(60))
            p.drawText(QRect(tile.left() + int(S(30)), tile.top(), tw, int(S(TITLE_H))), Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(f).elidedText(title, Qt.ElideRight, tw))
            if hov:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#c42b1c") if self._hover[1] else rgba(fg, 30))
                p.drawRoundedRect(QRectF(close), S(6), S(6))
                p.setPen(fg)
                p.setFont(icon_font(S(9)))
                p.drawText(QRectF(close), Qt.AlignCenter, "")
            if user32.IsIconic(h):
                p.setPen(dim)
                p.setFont(font(S(11)))
                p.drawText(QRect(tile.left(), tile.top() + int(S(TITLE_H)), tile.width(), int(S(TILE_H))),
                           Qt.AlignCenter, "Minimised")
