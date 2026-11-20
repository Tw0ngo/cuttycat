"""Smaller widgets: sidebar, level meter, track table, message bar."""
import numpy as np
from PySide6.QtCore import QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPalette, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QPushButton, QSizePolicy, QSlider, QStyle, QTableWidget,
                               QToolButton, QWidget)

from .common import DATA
from .theme import icon


class SideBar(QWidget):
    """Vertical icon bar with a classic Windows blue gradient."""
    changed = Signal(int)
    H = 70

    def __init__(self, items):
        super().__init__()
        self.items, self.cur, self.hover = items, 0, -1
        self.setFixedWidth(94)
        self.setMouseTracking(True)

    def set_current(self, i):
        self.cur = i
        self.update()
        self.changed.emit(i)

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        g = QLinearGradient(0, 0, r.width(), 0)
        dark = self.T.get("dark") if hasattr(self, "T") else False
        g.setColorAt(0, QColor(0, 0, 70) if dark else QColor(0, 0, 128))
        g.setColorAt(1, QColor(10, 80, 140) if dark else QColor(16, 132, 208))
        p.fillRect(r, g)
        p.setPen(QColor(0, 0, 64))
        p.drawLine(r.right(), 0, r.right(), r.height())
        bold = QFont(self.font())
        bold.setBold(True)
        p.setFont(bold)
        for i, (ic, label) in enumerate(self.items):
            rr = QRect(3, i * self.H + 3, r.width() - 7, self.H - 4)
            if i == self.cur:
                p.fillRect(rr, QColor(255, 255, 255, 46))
                p.setPen(QColor(255, 255, 255, 150))
                p.drawLine(rr.topLeft(), rr.topRight())
                p.drawLine(rr.topLeft(), rr.bottomLeft())
                p.setPen(QColor(0, 0, 40, 170))
                p.drawLine(rr.bottomLeft(), rr.bottomRight())
                p.drawLine(rr.topRight(), rr.bottomRight())
            elif i == self.hover:
                p.fillRect(rr, QColor(255, 255, 255, 22))
            ic.paint(p, QRect(rr.center().x() - 16, rr.top() + 5, 32, 32))
            tr = QRect(rr.left(), rr.top() + 41, rr.width(), 22)
            p.setPen(QColor(0, 0, 0, 150))
            p.drawText(tr.translated(1, 1), Qt.AlignHCenter | Qt.AlignTop, label)
            p.setPen(QColor(255, 255, 255))
            p.drawText(tr, Qt.AlignHCenter | Qt.AlignTop, label)

    def _idx(self, y):
        i = int(y // self.H)
        return i if 0 <= i < len(self.items) else -1

    def mousePressEvent(self, e):
        i = self._idx(e.position().y())
        if i >= 0:
            self.set_current(i)

    def mouseMoveEvent(self, e):
        i = self._idx(e.position().y())
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()


class Analyzer(QWidget):
    """Strawberry-style block analyzer on a dotted background."""

    def __init__(self, T):
        super().__init__()
        self.T = T
        self.levels = np.zeros(18)
        self.peaks = np.zeros(18)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def feed(self, lv):
        self.levels = np.maximum(lv, self.levels * 0.78)
        self.peaks = np.maximum(self.levels, self.peaks - 0.025)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        base = self.T["window"]
        p.fillRect(self.rect(), base)
        p.setPen(base.darker(118))
        for y in range(1, h, 2):
            for x in range(y % 4 // 2, w, 2):
                p.drawPoint(x, y)
        bw, gap, bh, bg = 4, 1, 2, 1
        rows = h // (bh + bg)
        blue, peak = QColor(32, 32, 224), QColor(120, 120, 255)
        for k, (lv, pk) in enumerate(zip(self.levels, self.peaks)):
            x = 4 + k * (bw + gap)
            n = int(lv * rows)
            for r in range(n):
                p.fillRect(x, h - (r + 1) * (bh + bg), bw, bh, blue)
            pr = int(pk * rows)
            if pr > n:
                p.fillRect(x, h - pr * (bh + bg), bw, bh, peak)


class VolumeWedge(QWidget):
    """Wedge-shaped volume control like Strawberry's."""
    changed = Signal(float)

    def __init__(self, T, value=0.5):
        super().__init__()
        self.T, self.value = T, value
        self.setFixedSize(116, 36)
        self.setToolTip("Volume")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        x0, x1, yb, yt = 6, w - 6, h - 10, 6
        p.setPen(QPen(self.T["window"].darker(160), 1))
        p.setBrush(self.T["base"])
        p.drawPolygon(QPolygonF([QPointF(x0, yb), QPointF(x1, yb), QPointF(x1, yt)]))
        xv = x0 + (x1 - x0) * self.value
        yv = yb - (yb - yt) * self.value
        g = QLinearGradient(x0, 0, x1, 0)
        g.setColorAt(0, self.T["sel"].lighter(170))
        g.setColorAt(1, self.T["sel"])
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x0, yb), QPointF(xv, yb), QPointF(xv, yv)]))
        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(QPolygonF([QPointF(xv, yb + 1), QPointF(xv - 5, h - 1), QPointF(xv + 5, h - 1)]))
        f = QFont(self.font())
        f.setPointSize(max(f.pointSize() - 1, 7))
        p.setFont(f)
        p.setPen(self.T["wtext"])
        p.drawText(QRect(0, 0, w // 2, 14), Qt.AlignCenter, f"{int(self.value * 100)}%")

    def _set(self, x):
        self.value = min(max((x - 6) / (self.width() - 12), 0), 1)
        self.changed.emit(self.value)
        self.update()

    def mousePressEvent(self, e):
        self._set(e.position().x())

    def mouseMoveEvent(self, e):
        self._set(e.position().x())

    def wheelEvent(self, e):
        self._set(6 + (self.value + e.angleDelta().y() / 120 * 0.05) * (self.width() - 12))


class TrackTable(QTableWidget):
    """Track list with a faded background picture (like Strawberry's playlist)."""
    COLS = ["", "#", "Artist", "Title", "Start", "End", "Length", "Gap"]
    C_ICON, C_NUM, C_ARTIST, C_TITLE, C_START, C_END, C_LEN, C_GAP = range(8)
    BG = DATA / "cat.png"

    def __init__(self, T):
        super().__init__(0, len(self.COLS))
        self.T = T
        self.cover = None
        self.setHorizontalHeaderLabels(self.COLS)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(self.C_TITLE, QHeaderView.Stretch)
        hh.setHighlightSections(False)
        for c, w in ((0, 26), (1, 42), (2, 220), (4, 92), (5, 92), (6, 82), (7, 70)):
            self.setColumnWidth(c, w)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(QFontMetrics(T["font"]).height() + 4)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setIconSize(QSize(14, 14))
        self.refresh_palette()
        self.viewport().setAutoFillBackground(False)

    def refresh_palette(self):
        T = self.T
        pal = QApplication.palette()
        b = QColor(T["base"])
        b.setAlpha(175)
        pal.setColor(QPalette.Base, b)
        a = QColor(T["alt"])
        a.setAlpha(205)
        pal.setColor(QPalette.AlternateBase, a)
        self.setPalette(pal)
        self.viewport().setPalette(pal)
        self.viewport().update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2) and self.state() != QAbstractItemView.EditingState:
            r, c = self.currentRow(), self.currentColumn()
            if r >= 0:
                col = c if (c == self.C_ARTIST or (c == self.C_START and r > 0)) else self.C_TITLE
                self.setCurrentCell(r, col)
                self.editItem(self.item(r, col))
                return
        super().keyPressEvent(e)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.viewport().update()  # keep the background fixed

    def paintEvent(self, e):
        p = QPainter(self.viewport())
        vr = self.viewport().rect()
        p.fillRect(vr, self.T["base"])
        if not hasattr(self, "_bg"):
            self._bg = QPixmap(str(self.BG)) if self.BG.exists() else QPixmap()
        if not self._bg.isNull():
            s = max(int(vr.height() * 0.95 / self._bg.height()), 1)  # integer scaling only
            pm = self._bg.scaled(self._bg.width() * s, self._bg.height() * s,
                                 Qt.KeepAspectRatio, Qt.FastTransformation)
            p.setOpacity(0.32 if self.T.get("dark") else 0.5)
            p.drawPixmap(vr.right() - pm.width() - 8, max(vr.bottom() - pm.height(), 0), pm)
        elif self.cover and not self.cover.isNull():
            pm = self.cover.scaled(vr.size() * 0.95, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.setOpacity(0.2)
            p.drawPixmap(vr.right() - pm.width() - 10, vr.bottom() - pm.height() - 4, pm)
        p.end()
        super().paintEvent(e)


class JumpSlider(QSlider):
    """Slider that jumps to where you click instead of paging."""
    jumped = Signal(int)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            from PySide6.QtWidgets import QStyleOptionSlider
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
            if not handle.contains(e.position().toPoint()):
                span = max(self.width() - handle.width(), 1)
                x = e.position().x() - handle.width() / 2
                v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(x), span)
                self.setValue(v)
                self.jumped.emit(v)
        super().mousePressEvent(e)


class MsgBar(QFrame):
    """Message strip under the toolbar (like KDE's KMessageWidget)."""
    dark = False
    DARK = {"info": (46, 54, 80), "ok": (40, 66, 44), "warn": (86, 66, 30), "error": (92, 40, 44)}
    COLORS = {"info": (214, 220, 236), "ok": (206, 228, 196), "warn": (248, 226, 180),
              "error": (244, 196, 196)}

    def __init__(self):
        super().__init__()
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 4, 4)
        self.ic = QLabel()
        self.text = QLabel(wordWrap=True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(260)
        self.bar.setMaximumHeight(18)
        self.btn = QPushButton()
        self.close_btn = QToolButton(text="✕", autoRaise=True)
        self.close_btn.setToolTip("Dismiss")
        self.close_btn.clicked.connect(self.hide)
        h.addWidget(self.ic)
        h.addWidget(self.text, 1)
        h.addWidget(self.bar)
        h.addWidget(self.btn)
        h.addWidget(self.close_btn)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._slot = None
        self.btn.clicked.connect(lambda: self._slot and self._slot())
        self.hide()

    def show_msg(self, text, level="info", ms=None, action=None, busy=False):
        r, g, b = (self.DARK if self.dark else self.COLORS)[level]
        edge = (min(r * 2, 255), min(g * 2, 255), min(b * 2, 255)) if self.dark else (r // 2, g // 2, b // 2)
        fg = "#f0ebe2" if self.dark else "#181612"
        self.setStyleSheet(f"MsgBar{{background: rgb({r},{g},{b}); border: 1px solid rgb{edge};}}"
                           f"MsgBar QLabel{{color: {fg};}}")
        self.ic.setPixmap(icon({"info": "dialog-information", "ok": "dialog-ok-apply",
                                "warn": "dialog-warning", "error": "dialog-error"}[level],
                               "dialog-information").pixmap(22, 22))
        self.text.setText(text)
        self.bar.setVisible(busy)
        if busy:
            self.bar.setRange(0, 0)
        if action:
            self.btn.setText(action[0])
            self._slot = action[1]
        self.btn.setVisible(bool(action))
        self.close_btn.setVisible(not busy)
        self.show()
        if ms:
            self.timer.start(ms)
        else:
            self.timer.stop()

    def set_progress(self, value, maximum):
        self.bar.setRange(0, maximum)
        self.bar.setValue(value)
