"""The waveform editor with the cuts."""
import time

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QMenu, QWidget

from .theme import icon
from .tracklist import fmt


class Waveform(QWidget):
    seek = Signal(float)
    preview = Signal(float)
    view_changed = Signal()
    open_requested = Signal()
    play_at = Signal(float)
    hint = Signal(str)
    RULER = 18
    HINT = ("Click: jump here  ·  Drag: scroll  ·  Wheel: zoom  ·  Double-click: add cut  ·  "
            "Drag red line: move cut  ·  Right-click: more")

    def __init__(self, album, T):
        super().__init__()
        self.m, self.T = album, T
        self.v0, self.v1 = 0.0, 1.0
        self.playhead = 0.0
        self.thresh = -40.0
        self.sel_track = -1
        self.drag = None
        self.drag_snap = None
        self.press = None
        self.panning = False
        self.hover_marker = None
        self.loading_text = ""
        self.user_view_t = 0.0
        self._hint = ""
        self.setMouseTracking(True)
        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.ClickFocus)

    # view
    def span(self):
        return self.v1 - self.v0

    def x2t(self, x):
        return self.v0 + x / max(self.width(), 1) * self.span()

    def t2x(self, t):
        return (t - self.v0) / self.span() * self.width()

    def set_view(self, v0, v1, user=True):
        if user:
            self.user_view_t = time.monotonic()
        tot = max(self.m.total, 1.0)
        span = min(max(v1 - v0, 0.5), tot)
        v0 = min(max(v0, 0.0), tot - span)
        self.v0, self.v1 = v0, v0 + span
        self.view_changed.emit()
        self.update()

    def reset_view(self):
        self.set_view(0, max(self.m.total, 1.0))

    def zoom(self, factor, center=None):
        c = self.x2t(self.width() / 2) if center is None else center
        rel = (c - self.v0) / self.span()
        span = self.span() * factor
        self.set_view(c - rel * span, c - rel * span + span)

    def show_time(self, t, span=None):
        span = span or self.span()
        self.set_view(t - span / 2, t + span / 2)

    def ensure_visible(self, t):
        if not (self.v0 <= t <= self.v1):
            self.set_view(t - self.span() * 0.1, t + self.span() * 0.9)

    # painting
    def paintEvent(self, e):
        p = QPainter(self)
        T, m = self.T, self.m
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), T["base"])
        top = self.RULER
        rg = QLinearGradient(0, 0, 0, top)
        rg.setColorAt(0, T["window"].lighter(135))
        rg.setColorAt(1, T["window"].lighter(110))
        p.fillRect(0, 0, W, top, rg)
        p.setPen(T["window"].darker(150))
        p.drawLine(0, top - 1, W, top - 1)
        if self.loading_text or m.env is None:
            big = QFont(self.font())
            big.setPointSize(big.pointSize() + 2)
            p.setFont(big)
            p.setPen(T["text"] if self.loading_text else T["inactive"])
            p.drawText(self.rect().adjusted(0, top, 0, 0), Qt.AlignCenter,
                       self.loading_text or "Click to open an audio file, or drop one anywhere in the window.")
            return
        mid = top + (H - top) / 2
        amp = (H - top) / 2 * 0.92
        # track regions
        alt = QColor(T["alt"])
        alt.setAlpha(140)
        for i in range(len(m.tracks)):
            a, b = m.tracks[i][0], m.end(i)
            if b < self.v0 or a > self.v1:
                continue
            xa, xb = self.t2x(a), self.t2x(b)
            if i == self.sel_track:
                c = QColor(T["sel"])
                c.setAlpha(40)
                p.fillRect(QRectF(xa, top, xb - xa, H - top), c)
            elif i % 2:
                p.fillRect(QRectF(xa, top, xb - xa, H - top), alt)
        # gap candidates
        cc = QColor(T["accent"])
        cc.setAlpha(170)
        unused = QColor(T["accent"])
        unused.setAlpha(80)
        hatch = QBrush(T["accent"].darker(135), Qt.BDiagPattern)
        cuts = [x[0] for x in m.tracks[1:]]
        for a, b in m.candidates:
            if b < self.v0 or a > self.v1:
                continue
            xa, xb = self.t2x(a), self.t2x(b)
            r = QRectF(xa, top, max(xb - xa, 1.5), H - top)
            if any(a - 0.05 <= c <= b + 0.05 for c in cuts):
                p.fillRect(r, cc)
            else:  # found but not used: hatched
                p.fillRect(r, unused)
                p.fillRect(r, hatch)
        # envelope
        b0 = max(int(self.v0 * 100), 0)
        b1 = min(int(self.v1 * 100) + 2, m.env.size)
        seg = m.env[b0:b1]
        if seg.size and W > 0:
            idx = np.minimum((np.arange(W) * (seg.size / W)).astype(int), seg.size - 1)
            vals = np.maximum.reduceat(seg, idx) if seg.size > W else seg[idx]
            px = self.t2x(self.playhead)
            played, rest = [], []
            for x, v in enumerate(vals):
                h = max(float(v) * amp, 0.5)
                (played if x < px else rest).append(QLineF(x + .5, mid - h, x + .5, mid + h))
            p.setPen(QPen(T["sel"].darker(135), 1))
            p.drawLines(played)
            p.setPen(QPen(T["sel"], 1))
            p.drawLines(rest)
        # threshold
        fm = QFontMetrics(self.font())
        lo, hi = m.disp
        tn = min(max((self.thresh - lo) / (hi - lo), 0), 1) * amp
        p.setPen(QPen(T["neutral"], 1, Qt.DashLine))
        p.drawLine(QLineF(0, mid - tn, W, mid - tn))
        p.drawLine(QLineF(0, mid + tn, W, mid + tn))
        lbl = "silence level"
        lr = QRectF(4, mid - tn - fm.height() - 2, fm.horizontalAdvance(lbl) + 8, fm.height())
        p.fillRect(lr, T["base"])
        p.setPen(T["neutral"].darker(160))
        p.drawText(lr, Qt.AlignCenter, lbl)
        # ruler ticks
        p.setPen(T["wtext"])
        fm = QFontMetrics(self.font())
        step = next((s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200)
                     if s / self.span() * W >= fm.horizontalAdvance("00:00.0") + 24), 1800)
        t = (self.v0 // step) * step
        while t <= self.v1:
            x = self.t2x(t)
            p.drawLine(QLineF(x, top - 5, x, top - 1))
            p.drawText(QPointF(x + 3, top - 5), fmt(t, 1 if step < 1 else 0))
            t += step
        # markers + titles
        for i in range(len(m.tracks)):
            a = m.tracks[i][0]
            if m.end(i) < self.v0 or a > self.v1:
                continue
            x = self.t2x(a)
            label = f"{i + 1:02d}"
            fw = fm.horizontalAdvance(label) + 8
            xb = self.t2x(m.end(i))
            if i > 0:
                w = 3 if i in (self.drag, self.hover_marker) else 2
                p.setPen(QPen(T["neg"], w))
                p.drawLine(QLineF(x, top, x, H))
            flag = QRectF(x, top, fw, fm.height() + 2)
            p.fillRect(flag, T["neg"] if i else T["sel"])
            p.setPen(QColor(255, 255, 255))
            p.drawText(flag, Qt.AlignCenter, label)
            avail = int(min(xb, W) - max(x, 0) - fw - 10)
            if avail > 30:
                p.setPen(T["text"])
                p.drawText(QPointF(max(x, 0) + fw + 5, top + fm.ascent() + 1),
                           fm.elidedText(m.label(i), Qt.ElideRight, avail))
        # playhead
        x = self.t2x(self.playhead)
        if 0 <= x <= W:
            p.setPen(QPen(QColor(0, 0, 0), 1))
            p.drawLine(QLineF(x, top, x, H))
            p.setBrush(QColor(0, 0, 0))
            p.drawPolygon(QPolygonF([QPointF(x - 5, top - 8), QPointF(x + 5, top - 8),
                                     QPointF(x, top - 1)]))

    # mouse
    def marker_at(self, x):
        best, bd = None, 6.0
        for i in range(1, len(self.m.tracks)):
            d = abs(self.t2x(self.m.tracks[i][0]) - x)
            if d <= bd:
                best, bd = i, d
        return best

    def mousePressEvent(self, e):
        if self.m.env is None or self.loading_text:
            if e.button() == Qt.LeftButton and not self.loading_text:
                self.open_requested.emit()
            return
        x = e.position().x()
        if e.button() == Qt.LeftButton:
            i = self.marker_at(x)
            if i:
                self.drag, self.drag_snap = i, self.m.snapshot()
            else:
                self.press = (x, self.v0, self.v1)
                self.panning = False
        elif e.button() == Qt.MiddleButton:
            self.press = (x, self.v0, self.v1)
            self.panning = True

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.drag:
            self.m.move_cut(self.drag, self.x2t(x))
        elif self.press:
            dx = x - self.press[0]
            if abs(dx) > 3:
                self.panning = True
            if self.panning:
                span = self.press[2] - self.press[1]
                v0 = self.press[1] - dx / max(self.width(), 1) * span
                self.set_view(v0, v0 + span)
        else:
            if self.m.env is None:
                self.setCursor(Qt.PointingHandCursor)
                return
            i = self.marker_at(x)
            if i != self.hover_marker:
                self.hover_marker = i
                self.setCursor(Qt.SizeHorCursor if i else Qt.ArrowCursor)
                self.update()
            t = self.x2t(x)
            gap = next(((a, b) for a, b in self.m.candidates if a <= t <= b), None)
            if i:
                h = f"Start of track {i + 1}  ·  Drag: move (Shift: no snapping)  ·  Right-click: listen, remove"
            elif gap:
                h = f"Gap, {gap[1] - gap[0]:.1f} s  ·  Double-click: add cut"
            else:
                h = self.HINT
            if h != self._hint:
                self._hint = h
                self.hint.emit(h)

    def leaveEvent(self, e):
        self._hint = self.HINT
        self.hint.emit(self.HINT)

    def mouseReleaseEvent(self, e):
        if self.drag:
            i = self.drag
            if not (e.modifiers() & Qt.ShiftModifier):
                self.m.move_cut(i, self.m.snap(self.m.tracks[i][0]), emit=False)
            if self.m.snapshot() != self.drag_snap:
                self.m.push_undo(self.drag_snap)
            self.drag = None
            self.m.changed.emit()
        elif self.press:
            if not self.panning:
                self.seek.emit(self.x2t(e.position().x()))
            self.press = None
            self.panning = False

    def mouseDoubleClickEvent(self, e):
        if self.m.env is None or e.button() != Qt.LeftButton:
            return
        t = self.x2t(e.position().x())
        if not self.marker_at(e.position().x()):
            self.m.add_cut(t if e.modifiers() & Qt.ShiftModifier else self.m.snap(t))

    def contextMenuEvent(self, e):
        if self.m.env is None:
            return
        x = e.pos().x()
        t = self.x2t(x)
        i = self.marker_at(x)
        menu = QMenu(self)
        if i:
            menu.addAction(icon("media-playback-start"), "Listen to cut",
                           lambda: self.preview.emit(self.m.tracks[i][0]))
            menu.addAction(icon("list-remove", "edit-delete"), "Remove cut",
                           lambda: self.m.remove_cuts([i]))
        else:
            menu.addAction(icon("edit-cut", "list-add"), "Add cut here",
                           lambda: self.m.add_cut(self.m.snap(t)))
            menu.addAction(icon("media-playback-start"), "Play from here",
                           lambda: self.play_at.emit(t))
        menu.addSeparator()
        menu.addAction(icon("zoom-fit-best", "zoom-original"), "Show whole file",
                       self.reset_view)
        menu.exec(e.globalPos())

    def wheelEvent(self, e):
        if self.m.env is None:
            return
        d = e.angleDelta()
        if e.modifiers() & Qt.ShiftModifier or abs(d.x()) > abs(d.y()):
            delta = d.x() or d.y()
            shift = -delta / 120 * self.span() * 0.1
            self.set_view(self.v0 + shift, self.v1 + shift)
        else:
            self.zoom(0.8 ** (d.y() / 120), self.x2t(e.position().x()))
