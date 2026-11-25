"""A small cat that sits beside the island.

It blinks now and then, bobs its head to the music, and curls up asleep after a few quiet
minutes (waking when you hover the island or a popup shows). It is drawn with shapes, so it is
sharp at any size, and it only repaints its own little rectangle: a few times a second while
music plays, once every few seconds otherwise.
"""
import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

from .theme import font

COATS = {
    #           fur          dark (stripes / ears)   belly
    "cat":    ("#a7a7ae", "#6e6e76", "#e7e7ea"),
    "ginger": ("#f0a35e", "#c46f2c", "#fde3c8"),
    "black":  ("#34343a", "#1d1d21", "#56565e"),
    "white":  ("#f4f4f6", "#c9c9cf", "#ffffff"),
}
SLEEP_AFTER = 180          # seconds without music, hover or popups


class Pet:
    W, H = 34, 30          # logical size before scaling

    def __init__(self, island):
        self.isl = island
        self.blink = False
        self.asleep = False
        self.bob = 0.0
        self.tail = 0.0
        self._last_active = time.monotonic()
        self._blink_t = QTimer(island, singleShot=True, timeout=self._do_blink)
        self._unblink_t = QTimer(island, singleShot=True, interval=140, timeout=self._end_blink)
        self._slow = QTimer(island, interval=4000, timeout=self._slow_tick)   # sleep check / zzz / tail
        self._blink_t.start(random.randint(2500, 6000))
        self._slow.start()
        self.rect = QRectF()

    def stop(self):
        for t in (self._blink_t, self._unblink_t, self._slow):
            t.stop()

    # ---------------------------------------------------------------- state
    def wake(self):
        self._last_active = time.monotonic()
        if self.asleep:
            self.asleep = False
            self.repaint()

    def _do_blink(self):
        if not self.asleep and self.isl.isVisible():
            self.blink = True
            self.repaint()
            self._unblink_t.start()
        self._blink_t.start(random.randint(2500, 7000))

    def _end_blink(self):
        self.blink = False
        self.repaint()

    def _slow_tick(self):
        m = self.isl.app.media
        if m.available and m.playing:
            self._last_active = time.monotonic()
        quiet = time.monotonic() - self._last_active
        if not self.asleep and quiet > SLEEP_AFTER:
            self.asleep = True
        self.tail = random.uniform(-1, 1) if not self.asleep else 0.0
        self.repaint()

    def music_frame(self, peak, n):
        """Called by the island's visualizer timer (~30 fps); the pet moves at ~8 fps."""
        if n % 4:
            return
        target = min(1.0, peak * 2.2)
        self.bob = self.bob * 0.4 + target * 0.6
        self.tail = math.sin(time.monotonic() * 3.0)
        self.repaint()

    def repaint(self):
        if not self.rect.isEmpty():
            self.isl.update(self.rect.toAlignedRect().adjusted(-2, -2, 2, 2))

    # ---------------------------------------------------------------- drawing
    _frames = {}

    def paint(self, p, rect):
        """Draw the cat with its feet on rect's bottom edge. Each pose is drawn once into a
        cached image (head bob and tail rounded to a few steps), then just copied."""
        self.rect = QRectF(rect)
        dpr = p.device().devicePixelRatioF() if p.device() else 1.0
        bob_q = round(self.bob * 4) / 4
        tail_q = round(self.tail * 2) / 2
        key = (self.isl.st["pet"], self.asleep, self.blink, bob_q, tail_q,
               round(rect.width() * dpr), round(rect.height() * dpr), self.isl.st.accent() if self.asleep else "")
        pm = Pet._frames.get(key)
        if pm is None:
            if len(Pet._frames) > 120:
                Pet._frames.clear()
            from PySide6.QtGui import QPainter, QPixmap
            pm = QPixmap(key[5], key[6])
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            bob, tail = self.bob, self.tail
            self.bob, self.tail = bob_q, tail_q
            self._draw(q, QRectF(0, 0, rect.width(), rect.height()))
            self.bob, self.tail = bob, tail
            q.end()
            Pet._frames[key] = pm
        p.drawPixmap(QPointF(round(rect.left() * dpr) / dpr, round(rect.top() * dpr) / dpr), pm)

    def _draw(self, p, rect):
        S = rect.width() / self.W
        fur, dark, belly = (QColor(c) for c in COATS.get(self.isl.st["pet"], COATS["cat"]))
        p.save()
        p.translate(rect.left(), rect.top())
        p.scale(S, S)
        p.setRenderHint(p.RenderHint.Antialiasing)
        dark_coat = self.isl.st["pet"] == "black"
        outline = QPen(QColor(255, 255, 255, 95) if dark_coat else QColor(0, 0, 0, 150), 1.1)
        self._eye = QColor("#d9e86a") if dark_coat else QColor(20, 20, 24)
        if self.asleep:
            self._paint_sleeping(p, fur, dark, belly, outline)
        else:
            self._paint_sitting(p, fur, dark, belly, outline)
        p.restore()

    def _paint_sitting(self, p, fur, dark, belly, outline):
        bob = self.bob * 2.2
        # tail, curling up from the right side of the body
        sway = self.tail * 3
        tail = QPainterPath(QPointF(22, 27))
        tail.cubicTo(QPointF(31, 27), QPointF(32 + sway, 18), QPointF(28 + sway, 13))
        p.setPen(QPen(QColor(0, 0, 0, 150), 5.2, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(tail)
        p.setPen(QPen(fur, 3.4, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(tail)
        # body
        p.setPen(outline)
        p.setBrush(fur)
        p.drawEllipse(QRectF(7, 15, 18, 14.5))
        p.setPen(Qt.NoPen)
        p.setBrush(belly)
        p.drawEllipse(QRectF(12, 19, 8, 9))
        # front paws
        p.setPen(outline)
        p.setBrush(belly)
        p.drawEllipse(QRectF(10.5, 26, 5, 3.5))
        p.drawEllipse(QRectF(16.5, 26, 5, 3.5))
        # head (bobs with the music)
        hy = 3 + bob
        p.setBrush(fur)
        for ear in ((QPointF(7.5, hy + 6), QPointF(9, hy - 1.5), QPointF(14, hy + 3)),
                    (QPointF(24.5, hy + 6), QPointF(23, hy - 1.5), QPointF(18, hy + 3))):
            path = QPainterPath(ear[0])
            path.lineTo(ear[1])
            path.lineTo(ear[2])
            path.closeSubpath()
            p.drawPath(path)
        p.drawEllipse(QRectF(6.5, hy + 1.5, 19, 15))
        # stripes on the forehead
        p.setPen(QPen(dark, 1.1, Qt.SolidLine, Qt.RoundCap))
        for dx in (-2, 0, 2):
            p.drawLine(QPointF(16 + dx, hy + 2.8), QPointF(16 + dx, hy + 5))
        # eyes
        if self.blink:
            p.setPen(QPen(self._eye, 1.1, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(10.6, hy + 9), QPointF(13.4, hy + 9))
            p.drawLine(QPointF(18.6, hy + 9), QPointF(21.4, hy + 9))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(self._eye)
            p.drawEllipse(QRectF(10.8, hy + 7, 2.8, 3.6))
            p.drawEllipse(QRectF(18.4, hy + 7, 2.8, 3.6))
            p.setBrush(QColor(255, 255, 255, 220))
            p.drawEllipse(QRectF(12.2, hy + 7.5, 1.0, 1.0))
            p.drawEllipse(QRectF(19.8, hy + 7.5, 1.0, 1.0))
        # nose + mouth
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ff8fa3"))
        p.drawEllipse(QRectF(15.1, hy + 10.6, 1.8, 1.3))
        # a little "ω" smile
        p.setPen(QPen(QColor(self._eye.red(), self._eye.green(), self._eye.blue(), 210), 0.8,
                      Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(14.2, hy + 11.0, 1.8, 1.8), 180 * 16, 180 * 16)
        p.drawArc(QRectF(16.0, hy + 11.0, 1.8, 1.8), 180 * 16, 180 * 16)

    def _paint_sleeping(self, p, fur, dark, belly, outline):
        # curled up: one round body with the head tucked in and the tail around it
        p.setPen(outline)
        p.setBrush(fur)
        p.drawEllipse(QRectF(4, 16, 26, 13.5))
        tail = QPainterPath(QPointF(28, 25))
        tail.cubicTo(QPointF(30, 30), QPointF(12, 31), QPointF(8, 27))
        p.setPen(QPen(QColor(0, 0, 0, 150), 4.6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(tail)
        p.setPen(QPen(fur, 3, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(tail)
        p.setPen(outline)
        p.setBrush(fur)
        ear = QPainterPath(QPointF(7, 19))
        ear.lineTo(QPointF(8.5, 13.5))
        ear.lineTo(QPointF(12.5, 17))
        ear.closeSubpath()
        p.drawPath(ear)
        p.drawEllipse(QRectF(5, 15.5, 12, 10))
        p.setPen(QPen(self._eye, 1.0, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(7.5, 19.5, 3, 2), 200 * 16, 140 * 16)
        p.drawArc(QRectF(11.5, 19.5, 3, 2), 200 * 16, 140 * 16)
        # z
        p.setPen(QColor(self.isl.st.accent()))
        p.setFont(font(7, QFont.DemiBold))
        p.drawText(QRectF(20, 3, 12, 10), Qt.AlignCenter, "z")
