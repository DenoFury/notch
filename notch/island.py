"""The island window: shape, spring animation, resting pill and HUD popups.

Performance notes:
- The window is only as big as the island (it grows for an animation and shrinks
  back when it settles), so every repaint touches as few pixels as possible.
- Hover uses the window's enter/leave events; the only polling is a short check
  while the card is open.
- Animation runs only while something moves, timed by real elapsed time so it
  stays smooth even if a frame arrives late.
- The music visualizer repaints just its own few pixels.
"""
import ctypes
import functools
import random

from PySide6.QtCore import QElapsedTimer, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from .theme import G, font, hide_from_capture, icon_font, rgba

user32 = ctypes.windll.user32
HWND_TOPMOST = -1
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE

EXPANDED = (540, 272)
HIT = QColor(0, 0, 0, 1)   # invisible but still catches the mouse


@functools.lru_cache(maxsize=64)
def elided(text, f_key, width):
    f = font(*f_key)
    return QFontMetrics(f).elidedText(text, Qt.ElideRight, int(width))


class Spring:
    """Critically-ish damped spring integrated on real elapsed time."""
    K = 560.0          # stiffness
    C = 2 * (560.0 ** 0.5) * 0.74   # damping (<1 gives a little bounce)

    def __init__(self, v):
        self.v = self.t = float(v)
        self.vel = 0.0

    def step(self, dt):
        h = 1 / 240
        while dt > 0:
            s = min(h, dt)
            a = -self.K * (self.v - self.t) - self.C * self.vel
            self.vel += a * s
            self.v += self.vel * s
            dt -= s
        if abs(self.t - self.v) < 0.3 and abs(self.vel) < 6:
            self.v, self.vel = self.t, 0.0
            return False
        return True


class Island(QWidget):
    def __init__(self, app, screen=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint)
        self.app = app
        self.scr = screen or QGuiApplication.primaryScreen()
        self.st = app.settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.ArrowCursor)
        self.setAcceptDrops(True)        # drop files on the island to park them on the shelf

        self.expanded = False
        self.pinned_open = False   # test mode: stay open without hover
        self.hud = None
        self._hud_timer = QTimer(self, singleShot=True, timeout=self.clear_hud)
        self._buttons = []
        self._bars = [0.15] * 4
        self._bars_rect = QRectF()
        self._fs_hidden = False
        self.card = None
        self._card_dirty = False
        self._fx = None
        self._fade = None

        self.w, self.h, self.r = Spring(150), Spring(32), Spring(16)
        self._clock = QElapsedTimer()
        self._anim = QTimer(self, timeout=self._frame)
        self._anim.setTimerType(Qt.PreciseTimer)
        rate = QGuiApplication.primaryScreen().refreshRate() or 60
        self._anim.setInterval(max(6, int(1000 / min(max(rate, 60), 144))))
        self._viz = QTimer(self, interval=33, timeout=self._viz_frame)

        self._open_timer = QTimer(self, singleShot=True, timeout=self._try_open)
        self._close_check = QTimer(self, interval=120, timeout=self._check_away)
        self._away = 0
        QTimer(self, interval=3000, timeout=self._keep_on_top).start()

        self._viz_n = 0
        self.pet = None
        self._make_pet()
        self.st.changed.connect(self._on_setting)
        self.retarget(snap=True)

    # ------------------------------------------------------------ geometry
    def S(self, v):
        return v * self.st["scale"]

    def ear(self):
        return self.S(10) if self.st["shape"] == "notch" else 0

    def top(self):
        return 0 if self.st["shape"] == "notch" else self.S(self.st["top_margin"])

    def _ix(self, w):
        """Left edge of an island `w` wide inside the window (centre, or against the side)."""
        pos = self.st["position"]
        if pos == "left":
            return 2 + self.ear()
        if pos == "right":
            return self.width() - 2 - self.ear() - w
        return (self.width() - w) / 2

    def island_rect(self):
        w, h = self.w.v, self.h.v
        return QRectF(self._ix(w), self.top(), w, h)

    def set_screen(self, screen):
        if screen is not None and screen is not self.scr:
            self.scr = screen
            self.setScreen(screen)
            self._fit_window(final=not self._anim.isActive(), force=True)

    def target_size(self):
        S = self.S
        if self.expanded:
            return S(EXPANDED[0]), S(EXPANDED[1]), S(30)
        if self.hud:
            hh = self.hud.get("h", 52)
            return S(self.hud.get("w", 340)), S(hh), S(hh / 2)
        m = self.app.media
        if m.available and m.playing:
            return (S(330 if self.st["show_title_in_pill"] else 220)), S(34), S(17)
        if self.app.recorder.recording:
            return S(170), S(34), S(17)
        if self.app.timer.running:
            return S(200), S(34), S(17)
        if self.st["hud_downloads"] and self.app.downloads.active:
            return S(280), S(34), S(17)
        return S(150), S(32), S(16)

    def _fit_window(self, final=False, force=False):
        """Fixed width (never moves sideways, which caused a one-frame jump);
        only the height follows the island, anchored at the top edge.
        Transparent parts let clicks through, so the extra width is harmless."""
        pad = 2
        W = int(self.S(EXPANDED[0]) * 1.1 + 2 * self.ear() + 2 * pad)
        W += W % 2   # even width keeps the island on whole pixels
        if final:
            h = self.h.t
        else:
            h = max(self.h.v, self.h.t * 1.08)   # room for the spring's overshoot
        H = int(self.top() + h + pad + 1)
        if not final:
            H = max(H, self.height())
        if force or (W, H) != (self.width(), self.height()):
            try:
                scr = self.scr.geometry()
            except RuntimeError:          # that monitor was unplugged or went to sleep
                self.scr = QGuiApplication.primaryScreen()
                scr = self.scr.geometry()
            pos, margin = self.st["position"], int(self.S(14))
            if pos == "left":
                x = scr.x() + margin
            elif pos == "right":
                x = scr.x() + scr.width() - W - margin
            else:
                x = scr.x() + (scr.width() - W) // 2
            self.setGeometry(x, scr.y(), W, H)
            self._place_card()   # the island re-centred inside the new size; keep the card on it

    def retarget(self, snap=False):
        tw, th, tr = self.target_size()
        self.w.t, self.h.t, self.r.t = tw, th, tr
        if snap:
            for s in (self.w, self.h, self.r):
                s.v, s.vel = s.t, 0.0
            self._fit_window(final=True)
            self.update()
        else:
            self._fit_window()
            if not self._anim.isActive():
                self._clock.start()
                self._anim.start()
        self._update_viz()

    # ------------------------------------------------------------ animation
    def _frame(self):
        dt = min(0.05, self._clock.restart() / 1000.0)
        moving = False
        for s in (self.w, self.h, self.r):
            moving |= s.step(dt)
        self._place_card()
        if not moving:
            self._anim.stop()
            self._fit_window(final=True)
        self.update()

    def _update_viz(self):
        m = self.app.media
        on = (not self.expanded and not self.hud and m.available and m.playing and self.isVisible())
        if on and not self._viz.isActive():
            self._viz.start()
        elif not on and self._viz.isActive():
            self._viz.stop()

    def _make_pet(self):
        if self.pet is not None:
            self.pet.stop()
            self.pet = None
        if self.st["pet"] != "none":
            from .pet import Pet
            self.pet = Pet(self)
        self.update()

    def _pet_rect(self, r):
        """Beside the resting island, on the side away from the screen edge."""
        S = self.S
        from .pet import Pet
        w, h = S(Pet.W), S(Pet.H)
        pos = self.st["position"]
        pv = self.app.privacy
        dots = (pv.mic or pv.cam) and self.st["hud_privacy"]
        dots_w = S(26) if dots else 0
        bottom = r.top() + min(r.height(), S(34)) + S(1)
        if pos == "left":
            x = r.right() + S(6) + dots_w
        elif pos == "right":
            x = r.left() - S(6) - w - dots_w
        else:
            x = r.left() - S(6) - w
        return QRectF(x, bottom - h, w, h)

    def _viz_frame(self):
        peak = self.app.audio.peak()
        if self.pet is not None:
            self._viz_n += 1
            self.pet.music_frame(peak, self._viz_n)
        for i in range(4):
            target = min(1.0, peak * (0.6 + random.random() * 0.9)) if peak > 0.005 else 0.15
            self._bars[i] += (target - self._bars[i]) * 0.45
        if not self._anim.isActive() and not self._bars_rect.isEmpty():
            self.update(self._bars_rect.toAlignedRect().adjusted(-1, -1, 1, 1))

    # ------------------------------------------------------------ card
    def _ensure_card(self):
        if self.card is None:
            from .card import Card
            self.card = Card(self)
            self.card.rebuild()
            self.card.hide()

    def _place_card(self):
        if self.card is None:
            return
        if not self.expanded:
            if self.card.isVisible():
                self.card.hide()
            return
        tw, th, _ = self.target_size()
        progress = (self.h.v - self.S(52)) / max(1, th - self.S(52))
        if progress > 0.8 and not self.card.isVisible():
            self.card.setGeometry(QRect(int(self._ix(tw)), int(self.top()), int(tw), int(th)))
            self._fade_in()
        elif self.card.isVisible():
            self.card.move(int(self._ix(tw)), int(self.top()))

    def _fade_in(self):
        self._fx = QGraphicsOpacityEffect(self.card)
        self._fx.setOpacity(0.0)
        self.card.setGraphicsEffect(self._fx)
        self.card.show()
        self._fade = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=140)
        self._fade.valueChanged.connect(lambda v: self._fx and self._fx.setOpacity(v))
        # the effect re-renders the card offscreen on every repaint: drop it once faded in
        self._fade.finished.connect(self._drop_effect)
        self._fade.start()

    def _drop_effect(self):
        self._fx = None
        if self.card is not None:
            self.card.setGraphicsEffect(None)

    def showEvent(self, e):
        self._update_viz()   # music may have started before the island was on screen
        hide_from_capture(self, self.st["hide_from_capture"])

    def _keep_on_top(self):
        if self.isVisible():
            user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)

    # ------------------------------------------------------------ hover
    def _cursor_inside(self, slack=0):
        p = self.mapFromGlobal(QCursor.pos())
        return self.rect().adjusted(-slack, -slack, slack, slack).contains(p)

    def enterEvent(self, e):
        if self.pet is not None:
            self.pet.wake()
        if not self.expanded and not self._hud_has_buttons():
            self._open_timer.start(max(0, int(self.st["open_delay_ms"])))
            if self.card is None:   # build it during the open delay, not on the first frame
                QTimer.singleShot(0, self._ensure_card)

    def leaveEvent(self, e):
        self._open_timer.stop()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            if not self.expanded:
                self._ensure_card()
                self.set_expanded(True)
            self.card.show_tab("files")

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            self._away = 0
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.app.shelf.add(paths)
            e.acceptProposedAction()

    def _try_open(self):
        if self._cursor_inside() and not self._hud_has_buttons():
            self.set_expanded(True)

    def _check_away(self):
        if self.pinned_open or QApplication.activePopupWidget() is not None:
            self._away = 0
            return
        self._away = 0 if self._cursor_inside(2) else self._away + 1
        if self._away >= 3:   # ~350 ms outside
            self.set_expanded(False)

    def _hud_has_buttons(self):
        return bool(self.hud and self.hud.get("buttons"))

    def set_expanded(self, on):
        if on == self.expanded:
            return
        self.expanded = on
        if on:
            self._ensure_card()
            self.card.refresh()
            self._away = 0
            self._close_check.start()
        else:
            self._close_check.stop()
            if self._card_dirty and self.card is not None:
                self._card_dirty = False
                QTimer.singleShot(0, self.card.rebuild)
            if self._fade:
                self._fade.stop()
            if self.card:
                self._drop_effect()
                self.card.hide()
            self.app.card_closed()
        self.retarget()

    # ------------------------------------------------------------ HUDs
    def show_hud(self, hud, seconds=2.0):
        if self.expanded:
            return
        if self._fs_hidden and not hud.get("urgent"):
            return
        self.hud = hud
        if self.pet is not None:
            self.pet.wake()
        self._hud_timer.start(int(seconds * 1000))
        self.retarget()

    def clear_hud(self):
        self.hud = None
        self._buttons = []
        self.retarget()

    # ------------------------------------------------------------ settings / fullscreen
    def accent_changed(self):
        """The album accent moved to a new cover's colour."""
        self.update()
        if self.card is not None:
            if self.expanded:
                self._card_dirty = True
            else:
                self.card.rebuild()

    def _on_setting(self, key):
        if key == "hide_from_capture":
            hide_from_capture(self, self.st[key])
            return
        if key == "pet":
            self._make_pet()
            return
        if key == "position":
            self._fit_window(final=True, force=True)
            self.update()
            return
        if key in ("scale", "shape", "top_margin", "surface", "accent", "card_tabs"):
            elided.cache_clear()
            if self.card is not None:
                self.card.rebuild()
            self.retarget(snap=True)
        else:
            self.retarget()

    def set_fullscreen(self, fs):
        hide = fs and self.st["hide_in_fullscreen"]
        self._fs_hidden = hide
        if hide and self.isVisible():
            self.hide()
        elif not hide and not self.isVisible():
            self.show()
        self._update_viz()

    # ------------------------------------------------------------ input
    def mousePressEvent(self, e):
        p = e.position()
        for rect, cb in self._buttons:
            if rect.contains(p):
                cb()
                return
        if self.hud and self.hud.get("on_click") and not self.expanded:
            self.hud["on_click"]()
            self.clear_hud()
            return
        if not self.expanded:
            self._open_timer.stop()
            self.set_expanded(True)

    def wheelEvent(self, e):
        if self.expanded:
            return
        step = 0.04 if e.angleDelta().y() > 0 else -0.04
        a = self.app.audio
        a.set_level(a.level + step)

    # ------------------------------------------------------------ painting
    def surface(self):
        fill, fg, dim, line = self.st.surface()
        return QColor(*fill), QColor(fg), QColor(dim), QColor(line)

    def shape_path(self, r, radius):
        path = QPainterPath()
        rad = min(radius, r.height() / 2, r.width() / 2)
        if self.st["shape"] == "notch":
            ear = min(self.ear(), r.height() / 3)
            x0, x1, y0, y1 = r.left(), r.right(), r.top(), r.bottom()
            path.moveTo(x0 - ear, y0)
            path.lineTo(x1 + ear, y0)
            path.quadTo(x1, y0, x1, y0 + ear)
            path.lineTo(x1, y1 - rad)
            path.quadTo(x1, y1, x1 - rad, y1)
            path.lineTo(x0 + rad, y1)
            path.quadTo(x0, y1, x0, y1 - rad)
            path.lineTo(x0, y0 + ear)
            path.quadTo(x0, y0, x0 - ear, y0)
            path.closeSubpath()
        else:
            path.addRoundedRect(r, rad, rad)
        return path

    def paintEvent(self, ev):
        if (self.pet is not None and not self.pet.rect.isEmpty()
                and self.pet.rect.adjusted(-3, -3, 3, 3).contains(QRectF(ev.rect()))):
            p = QPainter(self)            # only the pet moved: it sits outside the island
            self.pet.paint(p, self.pet.rect)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg, fg, dim, line = self.surface()
        r = self.island_rect()
        # hover catcher from the screen edge down to the island
        p.fillRect(QRectF(r.left(), 0, r.width(), r.bottom()), HIT)
        path = self.shape_path(r, self.r.v)
        p.fillPath(path, bg)
        if bg.alpha() < 255 or bg.lightness() > 200:
            p.setPen(QPen(rgba(line, 200), 1))
            p.drawPath(path)
        self._buttons = []
        self._bars_rect = QRectF()
        self._privacy_dots(p, r)
        if self.pet is not None and not self.expanded and self.w.v < self.S(460):
            pr = self._pet_rect(r)
            if pr.left() >= 0 and pr.right() <= self.width():
                self.pet.paint(p, pr)
        elif self.pet is not None:
            self.pet.rect = QRectF()
        if self.expanded:
            return
        tw, th, _ = self.target_size()
        closeness = 1 - min(1, (abs(tw - self.w.v) + abs(th - self.h.v)) / self.S(40))
        if closeness <= 0.05:
            return
        p.setOpacity(closeness)
        p.setClipPath(path)
        if self.hud:
            self.paint_hud(p, r, fg, dim)
        else:
            self.paint_rest(p, r, fg, dim)

    def _privacy_dots(self, p, r):
        """Orange dot = microphone in use, green = camera (like a phone), beside the island."""
        pv = self.app.privacy
        cols = ([QColor("#30d158")] if pv.cam else []) + ([QColor("#ff9f0a")] if pv.mic else [])
        if not cols or not self.st["hud_privacy"]:
            return
        d, gap = self.S(7), self.S(5)
        side = -1 if self.st["position"] == "right" else 1
        x = (r.right() + self.S(8)) if side > 0 else (r.left() - self.S(8) - d)
        cy = r.top() + min(r.height(), self.S(34)) / 2
        p.setPen(Qt.NoPen)
        for c in cols:
            p.setBrush(c)
            p.drawEllipse(QRectF(x, cy - d / 2, d, d))
            x += (d + gap) * side

    def paint_rest(self, p, r, fg, dim):
        S = self.S
        m = self.app.media
        accent = QColor(self.st.accent())
        cy = r.center().y()
        if self.app.recorder.recording:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ff453a"))
            d = S(10)
            p.drawEllipse(QRectF(r.left() + S(16), cy - d / 2, d, d))
            p.setPen(fg)
            p.setFont(font(S(12), QFont.DemiBold))
            p.drawText(QRectF(r.left() + S(34), r.top(), S(60), r.height()), Qt.AlignVCenter | Qt.AlignLeft, "REC")
            p.drawText(QRectF(r.left(), r.top(), r.width() - S(16), r.height()), Qt.AlignVCenter | Qt.AlignRight,
                       self.app.recorder.elapsed())
            self._buttons.append((QRectF(r), self.app.recorder.stop))   # click the pill to stop
        elif m.available and m.playing:
            a = S(22)
            art = QRectF(r.left() + S(8), cy - a / 2, a, a)
            if not m.art.isNull():
                clip = QPainterPath()
                clip.addRoundedRect(art, S(6), S(6))
                p.save()
                p.setClipPath(clip, Qt.IntersectClip)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                p.drawImage(art, m.art_small(int(a * self.devicePixelRatioF())))
                p.restore()
            else:
                p.setFont(icon_font(S(13)))
                p.setPen(accent)
                p.drawText(art, Qt.AlignCenter, G["music"])
            bw, gap, maxh = S(3), S(3), S(16)
            x = r.right() - S(14) - 4 * bw - 3 * gap
            self._bars_rect = QRectF(x, cy - maxh / 2, 4 * bw + 3 * gap, maxh)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            for i, v in enumerate(self._bars):
                h = max(S(3), maxh * v)
                p.drawRoundedRect(QRectF(x + i * (bw + gap), cy - h / 2, bw, h), bw / 2, bw / 2)
            if self.st["show_title_in_pill"]:
                left, right = art.right() + S(10), x - S(10)
                text = m.title + (f"  ·  {m.artist}" if m.artist else "")
                key = (S(12), QFont.DemiBold)
                p.setFont(font(*key))
                p.setPen(fg)
                p.drawText(QRectF(left, r.top(), right - left, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                           elided(text, key, int(right - left)))
        elif not self.app.timer.running and self.st["hud_downloads"] and self.app.downloads.active:
            from .indicators import human
            (name, size, speed), n = self.app.downloads.summary()
            p.setFont(icon_font(S(13)))
            p.setPen(accent)
            p.drawText(QRectF(r.left() + S(12), r.top(), S(20), r.height()), Qt.AlignCenter, G["download"])
            right_txt = human(size) + (f"  ·  {human(speed)}/s" if speed > 1024 else "")
            key2 = (S(11), QFont.Normal)
            p.setFont(font(*key2))
            p.setPen(dim)
            rw = QFontMetrics(font(*key2)).horizontalAdvance(right_txt)
            p.drawText(QRectF(r.right() - S(14) - rw, r.top(), rw, r.height()), Qt.AlignVCenter | Qt.AlignRight,
                       right_txt)
            key = (S(12), QFont.DemiBold)
            p.setFont(font(*key))
            p.setPen(fg)
            left = r.left() + S(38)
            width = r.right() - S(24) - rw - left
            label = name if n == 1 else f"{name} +{n - 1}"
            p.drawText(QRectF(left, r.top(), width, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       elided(label, key, int(width)))
        elif self.app.timer.running:
            t = self.app.timer
            p.setFont(icon_font(S(13)))
            p.setPen(accent)
            p.drawText(QRectF(r.left() + S(12), r.top(), S(20), r.height()), Qt.AlignCenter, G["timer"])
            p.setFont(font(S(13), QFont.DemiBold))
            p.setPen(fg)
            p.drawText(QRectF(r.left(), r.top(), r.width() - S(16), r.height()),
                       Qt.AlignVCenter | Qt.AlignRight, t.text())

    def _bar(self, p, rect, frac, accent, track):
        style = self.st["hud_style"]
        p.setPen(Qt.NoPen)
        if style == "segments":
            n, gap = 16, self.S(2.5)
            w = (rect.width() - gap * (n - 1)) / n
            lit = round(frac * n)
            for i in range(n):
                p.setBrush(accent if i < lit else track)
                p.drawRoundedRect(QRectF(rect.left() + i * (w + gap), rect.top() - self.S(1), w,
                                         rect.height() + self.S(2)), self.S(1.5), self.S(1.5))
            return
        if style == "dots":
            n = 10
            d = min(rect.height() + self.S(2), rect.width() / n * 0.62)
            step = (rect.width() - d) / (n - 1)
            lit = round(frac * n)
            for i in range(n):
                p.setBrush(accent if i < lit else track)
                p.drawEllipse(QRectF(rect.left() + i * step, rect.center().y() - d / 2, d, d))
            return
        if style == "thin":
            rect = QRectF(rect.left(), rect.center().y() - self.S(1.5), rect.width(), self.S(3))
        rad = rect.height() / 2
        p.setBrush(track)
        p.drawRoundedRect(rect, rad, rad)
        if frac > 0:
            fr = QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * frac), rect.height())
            p.setBrush(accent)
            p.drawRoundedRect(fr, rad, rad)

    def paint_hud(self, p, r, fg, dim):
        S, h = self.S, self.hud
        accent = QColor(h.get("color") or self.st.accent())
        cy = r.center().y()
        pad = S(16)
        kind = h["kind"]
        if kind == "level":
            p.setFont(icon_font(S(17)))
            p.setPen(fg)
            p.drawText(QRectF(r.left() + pad, r.top(), S(22), r.height()), Qt.AlignCenter, h["glyph"])
            p.setFont(font(S(13), QFont.DemiBold))
            pct = QRectF(r.right() - pad - S(40), r.top(), S(40), r.height())
            p.drawText(pct, Qt.AlignVCenter | Qt.AlignRight, h["text"])
            bar = QRectF(r.left() + pad + S(34), cy - S(3), pct.left() - (r.left() + pad + S(34)) - S(10), S(6))
            self._bar(p, bar, h["value"], accent, rgba(dim, 70))
        elif kind == "battery":
            bw, bh = S(28), S(14)
            body = QRectF(r.left() + pad, cy - bh / 2, bw, bh)
            p.setPen(QPen(rgba(fg, 200), S(1.4)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(body, S(3.5), S(3.5))
            p.fillRect(QRectF(body.right() + S(1.5), cy - S(3), S(2), S(6)), rgba(fg, 200))
            inner = body.adjusted(S(2.5), S(2.5), -S(2.5), -S(2.5))
            inner.setWidth(inner.width() * h["value"])
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(inner, S(1.5), S(1.5))
            p.setPen(fg)
            p.setFont(font(S(13), QFont.DemiBold))
            p.drawText(QRectF(body.right() + S(12), r.top(), r.width(), r.height()),
                       Qt.AlignVCenter | Qt.AlignLeft, h["title"])
            p.setPen(accent)
            p.drawText(QRectF(r.left(), r.top(), r.width() - pad, r.height()),
                       Qt.AlignVCenter | Qt.AlignRight, h["text"])
        else:  # message
            ic = S(30)
            icon = QRectF(r.left() + S(12), cy - ic / 2, ic, ic)
            p.setPen(Qt.NoPen)
            p.setBrush(rgba(accent, 45))
            p.drawEllipse(icon)
            p.setPen(accent)
            p.setFont(icon_font(S(14)))
            p.drawText(icon, Qt.AlignCenter, h.get("glyph", G["bell"]))
            right = r.right() - S(12)
            for label, cb in reversed(h.get("buttons", [])):
                f = font(S(12), QFont.DemiBold)
                p.setFont(f)
                tw = QFontMetrics(f).horizontalAdvance(label) + S(22)
                br = QRectF(right - tw, cy - S(13), tw, S(26))
                p.setPen(Qt.NoPen)
                p.setBrush(rgba(fg, 30))
                p.drawRoundedRect(br, S(13), S(13))
                p.setPen(fg)
                p.drawText(br, Qt.AlignCenter, label)
                self._buttons.append((br, cb))
                right = br.left() - S(6)
            left = icon.right() + S(12)
            width = right - left - S(6)
            lines = [(h.get("sub"), (S(11), QFont.Normal), dim, S(15)),
                     (h.get("title"), (S(13), QFont.DemiBold), fg, S(19)),
                     (h.get("body"), (S(12), QFont.Normal), dim, S(16))]
            lines = [ln for ln in lines if ln[0]]
            y = cy - sum(ln[3] for ln in lines) / 2
            for text, key, col, lh in lines:
                p.setFont(font(*key))
                p.setPen(col)
                p.drawText(QRectF(left, y, width, lh), Qt.AlignLeft | Qt.AlignVCenter,
                           elided(text, key, int(width)))
                y += lh


# ---------------------------------------------------------------- one island per screen
def screen_of_monitor(left, top):
    """The QScreen whose top-left corner is this monitor's (Qt keeps monitor origins in pixels)."""
    for s in QGuiApplication.screens():
        g = s.geometry()
        if g.x() == left and g.y() == top:
            return s
    return QGuiApplication.primaryScreen()


class IslandGroup:
    """The islands on screen: on the main screen, on every screen, or one that follows the
    screen you're working on. The rest of the app talks to this as if it were one island."""

    def __init__(self, app):
        self.app = app
        self.st = app.settings
        self.items = []
        self.rebuild()

    @property
    def main(self):
        return self.items[0]

    def _screens(self):
        mode = self.st["island_screens"]
        primary = QGuiApplication.primaryScreen()
        if mode == "all":
            return [primary] + [s for s in QGuiApplication.screens() if s is not primary]
        if mode == "follow":
            return [self.app.system.active_screen() or primary]
        return [primary]

    def rebuild(self):
        visible = any(i.isVisible() for i in self.items) or not self.items
        for i in self.items:
            i.set_expanded(False)
            i.hide()
            i.deleteLater()
        self.items = [Island(self.app, s) for s in self._screens()]
        if visible:
            for i in self.items:
                i.show()
        self.set_fullscreen(self.app.system.fullscreen)

    def follow(self):
        """The active window moved to another screen (follow mode)."""
        if self.st["island_screens"] == "follow":
            s = self.app.system.active_screen()
            if s is not None and not self.main.expanded:
                self.main.set_screen(s)

    # -- the single-island interface the app uses
    @property
    def expanded(self):
        return any(i.expanded for i in self.items)

    @property
    def hud(self):
        return next((i.hud for i in self.items if i.hud), None)

    def show(self):
        for i in self.items:
            i.show()

    def show_hud(self, hud, seconds=2.0):
        for i in self.items:
            i.show_hud(hud, seconds)

    def clear_hud(self):
        for i in self.items:
            i.clear_hud()

    def set_expanded(self, on):
        if on:
            self.main.set_expanded(True)
        else:
            for i in self.items:
                i.set_expanded(False)

    def retarget(self, *a):
        for i in self.items:
            i.retarget()

    def update(self, *a):
        for i in self.items:
            i.update()

    def accent_changed(self):
        for i in self.items:
            i.accent_changed()

    def set_fullscreen(self, fs):
        """Hide only the island on the screen that has the fullscreen app."""
        fs_screen = self.app.system.fullscreen_screen() if fs else None
        for i in self.items:
            i.set_fullscreen(fs and (fs_screen is None or i.scr is fs_screen))
