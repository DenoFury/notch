"""The ^ flyout on the notch taskbar: every running tray app as an icon grid."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .theme import font, rgba, safe_tip

COLS = 5


class TrayFlyout(QWidget):
    def __init__(self, bar):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.bar = bar
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.apps = []
        self.cells = []        # [(rect, app or None)]  None = "Windows tray" footer
        self.hover = None

    def S(self, v):
        return self.bar.S(v)

    def show_for(self, apps, anchor):
        """anchor: global point (centre of the ^ button, top of the bar)."""
        S = self.S
        self.apps = apps
        cell, pad, foot = S(40), S(10), S(30)
        cols = max(1, min(COLS, len(apps)))
        rows = (len(apps) + cols - 1) // cols
        w = max(pad * 2 + cols * cell, S(170))
        h = pad * 2 + rows * cell + foot + (S(4) if apps else 0)
        self.cells = []
        x0 = (w - cols * cell) / 2
        for n, a in enumerate(apps):
            r, c = divmod(n, cols)
            self.cells.append((QRectF(x0 + c * cell, pad + r * cell, cell, cell), a))
        self.cells.append((QRectF(pad, h - pad - foot + S(2), w - 2 * pad, foot - S(2)), None))
        self.resize(int(w), int(h))
        self.move(int(anchor.x() - w / 2), int(anchor.y() - h - S(8)))
        from .theme import hide_from_capture
        hide_from_capture(self, self.bar.st["hide_from_capture"])
        self.show()

    def _at(self, p):
        return next(((r, a) for r, a in self.cells if r.contains(p)), None)

    def mouseMoveEvent(self, e):
        hit = self._at(e.position())
        if hit is not self.hover:
            self.hover = hit
            if hit and hit[1] is not None:
                QToolTip.showText(e.globalPosition().toPoint(), safe_tip(hit[1].name), self)
            else:
                QToolTip.hideText()
            self.update()

    def mouseReleaseEvent(self, e):
        hit = self._at(e.position())
        if not self.rect().contains(e.position().toPoint()):
            return self.close()
        if hit is None:
            return
        app = hit[1]
        if app is None:
            self.close()
            return self.bar.app.taskbar_hider.peek()
        if e.button() == Qt.RightButton:
            gp = e.globalPosition().toPoint()
            self.close()
            return self.bar.tray_menu(app, gp)
        self.close()
        self.bar.open_tray_app(app)

    def paintEvent(self, _):
        S = self.S
        bg, fg, dim, line = self.bar.surface()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), S(16), S(16))
        p.fillPath(path, bg)
        p.setPen(QPen(rgba(line, 220), 1))
        p.drawPath(path)
        for r, a in self.cells:
            hov = self.hover is not None and self.hover[0] is r
            if hov:
                p.setPen(Qt.NoPen)
                p.setBrush(rgba(fg, 26))
                p.drawRoundedRect(r.adjusted(S(3), S(3), -S(3), -S(3)), S(9), S(9))
            if a is None:
                p.setPen(fg if hov else dim)
                p.setFont(font(S(11), QFont.Normal))
                p.drawText(r, Qt.AlignCenter, "Windows tray…")
                continue
            pm = a.pixmap()
            ic = S(22)
            ir = QRectF(r.center().x() - ic / 2, r.center().y() - ic / 2, ic, ic)
            if not pm.isNull():
                from .taskbar_core import draw_icon
                draw_icon(p, ir, pm, self.devicePixelRatioF())
            else:
                p.setPen(fg)
                p.setFont(font(S(12), QFont.DemiBold))
                p.drawText(ir, Qt.AlignCenter, a.name[:1])
        if self.apps:
            y = self.cells[-1][0].top() - S(3)
            p.setPen(QPen(rgba(fg, 30), 1))
            p.drawLine(QPointF(S(14), y), QPointF(self.width() - S(14), y))
