"""Cover art: online search, the cover dialog and the crop editor."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import urllib.parse
import urllib.request

import numpy as np
from PySide6.QtCore import QLineF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget)

from .common import http_get, keep
from .theme import icon
from .tracklist import plural


def to_jpeg(src):
    """QImage or image bytes -> JPEG bytes."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    img = src if isinstance(src, QImage) else QImage.fromData(src)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", 92)
    return bytes(ba)


class CoverSearch(QThread):
    """Look up album art on iTunes, Deezer and MusicBrainz/Cover Art Archive (no API keys)."""
    done = Signal(list)

    def __init__(self, artist, album):
        super().__init__()
        self.artist, self.album = artist.strip(), album.strip()
        self.failed = False

    def run(self):
        q = urllib.parse.quote(f"{self.artist} {self.album}".strip())
        found = []

        def itunes():
            d = json.loads(http_get(f"https://itunes.apple.com/search?term={q}&entity=album&limit=6"))
            return [("iTunes", f"{r.get('artistName', '')} – {r.get('collectionName', '')}",
                     r["artworkUrl100"].replace("100x100bb", "300x300bb"),
                     r["artworkUrl100"].replace("100x100bb", "1200x1200bb"))
                    for r in d.get("results", []) if r.get("artworkUrl100")]

        def deezer():
            d = json.loads(http_get(f"https://api.deezer.com/search/album?q={q}&limit=6"))
            return [("Deezer", f"{r.get('artist', {}).get('name', '')} – {r.get('title', '')}",
                     r["cover_medium"], r.get("cover_xl") or r["cover_big"])
                    for r in d.get("data", []) if r.get("cover_medium")]

        def musicbrainz():
            parts = []
            if self.album:
                parts.append(f'releasegroup:"{self.album}"')
            if self.artist:
                parts.append(f'artist:"{self.artist}"')
            mq = urllib.parse.quote(" AND ".join(parts))
            d = json.loads(http_get(f"https://musicbrainz.org/ws/2/release-group/?query={mq}&fmt=json&limit=4"))
            out = []
            for r in d.get("release-groups", []):
                who = ", ".join(a.get("name", "") for a in r.get("artist-credit", []) if isinstance(a, dict))
                base = f"https://coverartarchive.org/release-group/{r['id']}"
                out.append(("MusicBrainz", f"{who} – {r.get('title', '')}", base + "/front-250",
                            base + "/front-1200"))
            return out

        errors = 0
        with ThreadPoolExecutor(3) as ex:
            for fut in [ex.submit(f) for f in (itunes, deezer, musicbrainz)]:
                try:
                    found += fut.result()
                except Exception:
                    errors += 1
        self.failed = errors == 3

        def thumb(item):
            try:
                return item + (http_get(item[2], 8),)
            except Exception:
                return None

        with ThreadPoolExecutor(8) as ex:
            res = [r for r in ex.map(thumb, found) if r and r[4]]
        self.done.emit(res)


class Fetch(QThread):
    done = Signal(bytes, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            self.done.emit(http_get(self.url, 20), "")
        except Exception as e:
            self.done.emit(b"", str(e))


class CoverLabel(QLabel):
    """Cover thumbnail: click for options, drop an image file or link onto it."""
    clicked = Signal()
    dropped = Signal(bytes, str)

    def __init__(self, size):
        super().__init__()
        self.size_ = size
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to change, or drop an image here")
        self.setAcceptDrops(True)
        self.set_image(QPixmap())

    def set_image(self, pm):
        if pm and not pm.isNull():
            self.setPixmap(pm.scaled(self.size_, self.size_, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.setPixmap(QPixmap())
            self.setText("No cover\n(click to add)")

    def mousePressEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.RightButton):
            self.clicked.emit()

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasImage() or md.hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        for u in md.urls():
            if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in (".jpg", ".jpeg", ".png"):
                self.dropped.emit(Path(u.toLocalFile()).read_bytes(), Path(u.toLocalFile()).name)
                e.acceptProposedAction()
                return
        if md.hasImage():
            self.dropped.emit(to_jpeg(md.imageData()), "dropped image")
            e.acceptProposedAction()


class CoverDialog(QDialog):
    """Search iTunes / Deezer / MusicBrainz and pick a cover."""

    def __init__(self, parent, artist, album):
        super().__init__(parent)
        self.choice = None
        self.setWindowTitle("Find cover")
        self.resize(980, 620)
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.e_artist = QLineEdit(artist, placeholderText="Artist")
        self.e_album = QLineEdit(album, placeholderText="Album")
        b = self.b_search = QPushButton(icon("edit-find", "system-search"), " Search")
        b.clicked.connect(self.search)
        self.e_album.returnPressed.connect(self.search)
        self.e_artist.returnPressed.connect(self.search)
        row.addWidget(self.e_artist, 1)
        row.addWidget(self.e_album, 2)
        row.addWidget(b)
        v.addLayout(row)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setIconSize(QSize(180, 180))
        self.list.setGridSize(QSize(214, 290))
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda _: self.pick())
        v.addWidget(self.list, 1)
        self.status = QLabel()
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        self.ok = QPushButton("Use cover")
        self.ok.setDefault(True)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.pick)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(self.ok)
        row.addWidget(cancel)
        v.addLayout(row)
        self.list.currentItemChanged.connect(lambda cur, _: self.ok.setEnabled(cur is not None))
        self.results = []
        if artist or album:
            QTimer.singleShot(0, self.search)

    def search(self):
        if getattr(self, "worker", None) and self.worker.isRunning():
            return
        self.list.clear()
        self.b_search.setEnabled(False)
        self.status.setText("Searching iTunes, Deezer and MusicBrainz…")
        self.worker = CoverSearch(self.e_artist.text(), self.e_album.text())
        self.worker.done.connect(self.show_results)
        keep(self.worker)

    def show_results(self, res):
        self.results = res
        self.b_search.setEnabled(True)
        for src, label, _, full, thumb in res:
            pm = QPixmap()
            pm.loadFromData(thumb)
            it = QListWidgetItem(QIcon(pm), f"{src}\n{label}")
            it.setToolTip(f"{label}\nfrom {src}")
            self.list.addItem(it)
        self.status.setText(f"{plural(len(res), 'cover')} found. Double-click one to use it." if res
                            else ("Couldn't reach the cover services." if self.worker.failed
                  else "Nothing found. Try fewer words, or only the album name."))
        if res:
            self.list.setCurrentRow(0)

    def pick(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.results):
            self.choice = self.results[r]
            self.accept()


def trim_black_bars(img):
    """Remove black letterbox/pillarbox borders (common on YouTube thumbnails)."""
    img = img.convertToFormat(QImage.Format_RGB32)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w * 4]
    lum = arr.reshape(h, w, 4)[:, :, :3].mean(axis=2)
    rows = np.flatnonzero(lum.mean(axis=1) > 20)
    cols = np.flatnonzero(lum.mean(axis=0) > 20)
    if rows.size < h * 0.3 or cols.size < w * 0.3:
        return img
    return img.copy(int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1))


class CropView(QWidget):
    changed = Signal()

    def __init__(self, img):
        super().__init__()
        self.img = img
        self.pm = QPixmap.fromImage(img)
        self.reset()
        self.drag = None
        self.setMinimumSize(480, 320)
        self.setCursor(Qt.OpenHandCursor)

    def reset(self):
        self.side = min(self.img.width(), self.img.height())
        self.cx, self.cy = self.img.width() / 2, self.img.height() / 2
        self.changed.emit()
        self.update()

    def scale(self):
        return min(self.width() / self.img.width(), self.height() / self.img.height())

    def img_rect(self):
        s = self.scale()
        w, h = self.img.width() * s, self.img.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def clamp(self):
        W, H = self.img.width(), self.img.height()
        self.side = min(max(self.side, min(W, H) * 0.15), min(W, H))
        self.cx = min(max(self.cx, self.side / 2), W - self.side / 2)
        self.cy = min(max(self.cy, self.side / 2), H - self.side / 2)

    def crop(self):
        self.clamp()
        return QRect(int(self.cx - self.side / 2), int(self.cy - self.side / 2), int(self.side), int(self.side))

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        r = self.img_rect()
        p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        if not self.isEnabled():
            return
        s = self.scale()
        c = self.crop()
        cr = QRectF(r.left() + c.x() * s, r.top() + c.y() * s, c.width() * s, c.height() * s)
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), cr.top() - r.top()), shade)
        p.fillRect(QRectF(r.left(), cr.bottom(), r.width(), r.bottom() - cr.bottom()), shade)
        p.fillRect(QRectF(r.left(), cr.top(), cr.left() - r.left(), cr.height()), shade)
        p.fillRect(QRectF(cr.right(), cr.top(), r.right() - cr.right(), cr.height()), shade)
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for k in (1, 2):
            p.drawLine(QLineF(cr.left() + cr.width() * k / 3, cr.top(), cr.left() + cr.width() * k / 3, cr.bottom()))
            p.drawLine(QLineF(cr.left(), cr.top() + cr.height() * k / 3, cr.right(), cr.top() + cr.height() * k / 3))
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawRect(cr)

    def mousePressEvent(self, e):
        self.drag = (e.position(), self.cx, self.cy)
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.drag:
            d = (e.position() - self.drag[0]) / self.scale()
            self.cx, self.cy = self.drag[1] + d.x(), self.drag[2] + d.y()
            self.clamp()
            self.changed.emit()
            self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, e):
        self.side *= 0.92 ** (e.angleDelta().y() / 120)
        self.clamp()
        self.changed.emit()
        self.update()


class CoverEditor(QDialog):
    """Turn a (16:9) picture into a square cover: crop, or fit with blurred/black edges."""
    MODES = ["Crop to a square", "Whole picture, blurred edges", "Whole picture, black edges"]

    def __init__(self, parent, data, trim=False, keep_label="Cancel"):
        super().__init__(parent)
        self.setWindowTitle("Adjust cover")
        self.resize(960, 560)
        img = QImage.fromData(data)
        self.src = trim_black_bars(img) if trim else img
        self.result = b""
        h = QHBoxLayout(self)
        left = QVBoxLayout()
        self.view = CropView(self.src)
        self.view.changed.connect(self.update_preview)
        left.addWidget(self.view, 1)
        self.l_hint = QLabel("Drag to move the square · scroll to resize it")
        left.addWidget(self.l_hint)
        h.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Preview:"))
        self.preview = QLabel(alignment=Qt.AlignCenter)
        self.preview.setFixedSize(240, 240)
        self.preview.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        right.addWidget(self.preview)
        self.mode = QButtonGroup(self)
        for i, t in enumerate(self.MODES):
            rb = QRadioButton(t)
            self.mode.addButton(rb, i)
            right.addWidget(rb)
        self.mode.button(0).setChecked(True)
        self.mode.idClicked.connect(self.mode_changed)
        right.addWidget(QLabel("Size:"))
        self.size = QSlider(Qt.Horizontal, minimum=15, maximum=100, value=100)
        self.size.valueChanged.connect(self.size_changed)
        right.addWidget(self.size)
        reset = QPushButton(icon("edit-undo"), " Reset")
        reset.clicked.connect(lambda: (self.view.reset(), self.size.setValue(100)))
        right.addWidget(reset)
        right.addStretch(1)
        self.l_size = QLabel()
        right.addWidget(self.l_size)
        ok = QPushButton("Use cover")
        ok.setDefault(True)
        ok.clicked.connect(self.done_ok)
        cancel = QPushButton(keep_label)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(ok)
        row.addWidget(cancel)
        right.addLayout(row)
        h.addLayout(right)
        self.update_preview()

    def size_changed(self, v):
        self.view.side = min(self.src.width(), self.src.height()) * v / 100
        self.view.clamp()
        self.view.update()
        self.update_preview()

    def mode_changed(self, i):
        self.size.setEnabled(i == 0)
        self.view.setEnabled(i == 0)
        self.l_hint.setVisible(i == 0)
        self.view.update()
        self.update_preview()

    def render(self):
        img, m = self.src, self.mode.checkedId()
        if m == 0:
            c = self.view.crop()
            out = img.copy(c)
            if out.width() > 1400:
                out = out.scaled(1400, 1400, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return out
        side = min(max(img.width(), img.height()), 1400)
        out = QImage(side, side, QImage.Format_RGB32)
        out.fill(QColor(0, 0, 0))
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if m == 1:
            bg = img.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            bg = bg.copy((bg.width() - side) // 2, (bg.height() - side) // 2, side, side)
            bg = bg.scaled(28, 28, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
                   .scaled(side, side, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            p.drawImage(0, 0, bg)
            p.fillRect(out.rect(), QColor(0, 0, 0, 70))
        fg = img.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawImage((side - fg.width()) // 2, (side - fg.height()) // 2, fg)
        p.end()
        return out

    def update_preview(self):
        out = self.render()
        self.preview.setPixmap(QPixmap.fromImage(out).scaled(236, 236, Qt.KeepAspectRatio,
                                                             Qt.SmoothTransformation))
        self.l_size.setText(f"{out.width()}×{out.height()} px")

    def done_ok(self):
        self.result = to_jpeg(self.render())
        self.accept()
