"""Mini browser that drops down under the island (YouTube Music, YouTube, your pinned sites).

Qt WebEngine (Chromium) is big, so it is only loaded the first time you open the panel and the
whole browser is thrown away a minute after you close it. Sign-ins are kept in Notch's data
folder, so you stay logged in. (Spotify's web player needs DRM that Qt's browser doesn't have;
the Spotify app's controls are already on the island.)
"""
import os

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .config import DATA_DIR
from .theme import G, font, icon_font, rgba, safe_tip

SITES = [("YouTube Music", "https://music.youtube.com/"), ("YouTube", "https://www.youtube.com/"),
         ("SoundCloud", "https://soundcloud.com/"), ("Google", "https://www.google.com/")]

_profile = None


def profile():
    global _profile
    if _profile is None:
        from PySide6.QtWebEngineCore import QWebEngineProfile
        _profile = QWebEngineProfile("notch-web")
        path = os.path.join(DATA_DIR, "web")
        _profile.setPersistentStoragePath(path)
        _profile.setCachePath(os.path.join(path, "cache"))
        _profile.setHttpCacheMaximumSize(64 * 1024 * 1024)
        _profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    return _profile


class WebPanel(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.app = app
        self.st = app.settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        S = self.S
        bg, fg, dim, line = (QColor(*self.st.surface()[0]), QColor(self.st.surface()[1]),
                             QColor(self.st.surface()[2]), QColor(self.st.surface()[3]))
        self.bg, self.fg, self.line = bg, fg, line
        lay = QVBoxLayout(self)
        m = int(S(10))
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(int(S(8)))
        bar = QHBoxLayout()
        bar.setSpacing(int(S(4)))

        def btn(glyph, tip, cb, icon=True):
            b = QPushButton(glyph)
            b.setFont(icon_font(S(13)) if icon else font(S(12), QFont.DemiBold))
            b.setToolTip(safe_tip(tip))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton{{color:{fg.name()};background:transparent;border:none;"
                            f"border-radius:{int(S(14))}px;padding:{int(S(5))}px {int(S(9))}px;}}"
                            f"QPushButton:hover{{background:{rgba(fg, 40).name(QColor.HexArgb)};}}")
            b.clicked.connect(cb)
            bar.addWidget(b)
            return b
        btn("", "Back", lambda: self.view and self.view.back())
        btn("", "Forward", lambda: self.view and self.view.forward())
        btn("", "Reload", lambda: self.view and self.view.reload())
        self.addr = QLineEdit()
        self.addr.setFont(font(S(12)))
        self.addr.setPlaceholderText("Search or type an address")
        self.addr.setStyleSheet(f"QLineEdit{{background:{rgba(fg, 20).name(QColor.HexArgb)};color:{fg.name()};"
                                f"border:none;border-radius:{int(S(12))}px;padding:{int(S(5))}px {int(S(12))}px;}}")
        self.addr.returnPressed.connect(self._go)
        bar.addWidget(self.addr, 1)
        for name, url in SITES[:2] + [(p["name"], p["url"]) for p in (self.st["web_pins"] or [])][:4]:
            btn(name, url, lambda _=False, u=url: self.load(u), icon=False)
        btn(G["pin"], "Pin this site", self._pin)
        btn("", "Close (Esc)", self.close_panel)
        lay.addLayout(bar)

        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.view = QWebEngineView()
        self.view.setPage(QWebEnginePage(profile(), self.view))
        self.view.urlChanged.connect(lambda u: self.addr.setText(u.toString()))
        lay.addWidget(self.view, 1)
        self._free = QTimer(self, interval=60_000, timeout=self._maybe_release)

    def S(self, v):
        return v * self.st["scale"]

    def place_under(self, island):
        """Centred under the island, on the island's screen."""
        S = self.S
        w, h = int(S(900)), int(S(560))
        scr = island.scr.availableGeometry()
        r = island.island_rect()
        top_left = island.mapToGlobal(r.bottomLeft().toPoint())
        x = int(top_left.x() + r.width() / 2 - w / 2)
        x = max(scr.left() + 8, min(scr.right() - w - 8, x))
        self.setGeometry(x, int(island.geometry().top() + island.top() + S(10)), w, h)

    def load(self, url):
        self._free.stop()
        self.view.setUrl(QUrl(url))

    def _go(self):
        t = self.addr.text().strip()
        if not t:
            return
        if " " in t or "." not in t:
            from urllib.parse import quote_plus
            t = "https://www.google.com/search?q=" + quote_plus(t)
        elif "://" not in t:
            t = "https://" + t
        self.load(t)

    def _pin(self):
        u = self.view.url().toString()
        if not u:
            return
        name = self.view.title()[:14] or QUrl(u).host()
        pins = [p for p in (self.st["web_pins"] or []) if p["url"] != u]
        self.st.set("web_pins", ([{"name": name, "url": u}] + pins)[:4])

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()

    def close_panel(self):
        self.hide()
        self._free.start()        # music keeps playing; once it's quiet for a minute, free it all

    def _maybe_release(self):
        if self.isVisible():
            self._free.stop()
        elif not self.view.page().recentlyAudible():
            self._free.stop()
            self._release()

    def _release(self):
        global _profile
        self.app.web_panel_released()
        view, self.view = self.view, None
        view.setPage(None) if hasattr(view, "setPage") else None
        view.deleteLater()
        self.deleteLater()
        if _profile is not None:           # the profile owns Chromium's network/storage services
            prof, _profile = _profile, None
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1000, prof.deleteLater)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.S(22), self.S(22))
        p.fillPath(path, self.bg)
        p.setPen(rgba(self.line, 230))
        p.drawPath(path)
