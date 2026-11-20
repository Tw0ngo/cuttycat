#!/usr/bin/env python3
"""Cuttycat – split one long recording (album, mix, playlist rip) into tracks at the gaps.

See Help > Keyboard & mouse in the app for all controls.
"""
import configparser
import time
import shutil
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from .tracklist import (  # noqa: F401
    fmt, parse_time, plural, safe, guess_from_filename, TS, SEPARATORS, ORDER_TITLE,
    ORDER_ARTIST_TITLE, ORDER_TITLE_ARTIST, TIMES_AUTO, TIMES_START, TIMES_LENGTH, TIMES_IGNORE,
    parse_tracklist, yt_entries, description_tracklist)
from PySide6.QtCore import (QSettings, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer,
                            QUrl, Signal, QObject, QLineF)
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontMetrics, QIcon,
                           QKeySequence, QLinearGradient, QPainter, QPalette,
                           QPen, QPixmap, QPolygonF, QDesktopServices, QImage)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QInputDialog, QListWidget, QListWidgetItem,
                               QDialog, QFileDialog, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QRadioButton, QScrollBar,
                               QSizePolicy, QSlider, QSpinBox, QSplitter,
                               QStackedWidget, QStyleFactory, QTabBar, QTableWidget,
                               QTableWidgetItem, QToolBar, QToolButton, QTreeView, QStyle,
                               QVBoxLayout, QWidget, QAbstractItemView)
try:
    from PySide6.QtGui import QFileSystemModel
except ImportError:
    from PySide6.QtWidgets import QFileSystemModel
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

try:
    from . import __version__
except ImportError:
    __version__ = "dev"

APP = "Cuttycat"
DATA = Path(__file__).resolve().parent / "assets"
UA = "Cuttycat/1.0"
KOFI_URL = "https://ko-fi.com/twongo/?hidefeed=true&widget=true&embed=true"
EDGE = 5.0            # ignore gaps this close to start/end (seconds)
SR, BLK = 8000, 80    # analysis sample rate, block size -> 100 envelope values/s
AUDIO_EXT = ["*.mp3", "*.flac", "*.ogg", "*.opus", "*.m4a", "*.aac", "*.wav",
             "*.wma", "*.ape", "*.wv", "*.aiff", "*.aif", "*.mka", "*.webm",
             "*.mp4", "*.alac"]
FORMATS = [  # label, extension (None = same as source), audio codec args
    ("Original (no re-encoding)", None, None),
    ("FLAC", ".flac", ["-c:a", "flac"]),
    ("MP3 320 kbit/s", ".mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]),
    ("MP3 V0 (VBR)", ".mp3", ["-c:a", "libmp3lame", "-q:a", "0"]),
    ("Opus 160 kbit/s", ".opus", ["-c:a", "libopus", "-b:a", "160k"]),
    ("Ogg Vorbis q6", ".ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
]


# ───────────────────────────── helpers ─────────────────────────────


_THREADS = set()


def keep(thread):
    """Hold a reference until the thread finishes (a QThread destroyed while running aborts the app)."""
    _THREADS.add(thread)
    thread.finished.connect(lambda: _THREADS.discard(thread))
    thread.start()
    return thread


# Windows: no console window popping up for every ffmpeg call.
NOWIN = dict(creationflags=subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else {}
# Text output of ffprobe/yt-dlp is UTF-8 everywhere (Windows would otherwise decode it as cp1252).
UTF8 = dict(text=True, encoding="utf-8", errors="replace")


def yt_dlp_cmd():
    """System yt-dlp first (usually newest), then the copy bundled with the app."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    import importlib.util
    if importlib.util.find_spec("yt_dlp"):
        if getattr(sys, "frozen", False):          # standalone build: the app runs yt-dlp itself
            return [sys.executable, "--run-yt-dlp"]
        return [sys.executable, "-m", "yt_dlp"]
    return None


# ───────────────────────────── tracklist parser ─────────────────────────────


# ───────────────────────────── theme ─────────────────────────────

# Cuttycat's own retro palette – the same on every system.
PALETTE = dict(
    window=(143, 129, 110), button=(143, 129, 110), base=(217, 208, 201), alt=(230, 231, 232),
    text=(24, 22, 18), wtext=(24, 22, 18), sel=(62, 73, 118), seltext=(248, 248, 248),
    tip=(159, 143, 122), accent=(188, 173, 154), neg=(218, 68, 83), neutral=(246, 116, 0),
    inactive=(127, 140, 141),
)
# First installed family wins. Fonts dropped into assets/fonts/ are loaded automatically.
FONT_FAMILIES = ["DinaRemasterII", "DinaRemaster", "Dina", "Terminus", "Terminus (TTF)",
                 "Fixedsys Excelsior 3.01", "Px437 IBM VGA 8x16", "W95FA", "Monaco", "Consolas",
                 "DejaVu Sans Mono", "monospace"]
FONT_SIZE = 12


DARK_PALETTE = dict(
    window=(58, 52, 45), button=(74, 67, 58), base=(36, 34, 31), alt=(46, 43, 39),
    text=(226, 218, 206), wtext=(226, 218, 206), sel=(92, 110, 178), seltext=(250, 250, 250),
    tip=(74, 67, 58), accent=(112, 100, 84), neg=(236, 100, 108), neutral=(252, 156, 64),
    inactive=(150, 148, 140),
)


def system_prefers_dark(app=None):
    """KDE colour scheme first (text vs. background), then Qt/portal, then GTK settings."""
    home = Path.home() / ".config"

    def lum(v):
        r, g, b = (int(x) for x in v.split(",")[:3])
        return 0.299 * r + 0.587 * g + 0.114 * b

    try:
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.optionxform = str
        cfg.read(home / "kdeglobals")
        view = cfg["Colors:View"]
        return lum(view["ForegroundNormal"]) > lum(view["BackgroundNormal"])
    except Exception:
        pass
    try:
        app = app or QApplication.instance()
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    for f in (home / "gtk-4.0/settings.ini", home / "gtk-3.0/settings.ini"):
        try:
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.read(f)
            val = cfg["Settings"].get("gtk-application-prefer-dark-theme", "")
            if val.strip().lower() in ("1", "true"):
                return True
            if "dark" in cfg["Settings"].get("gtk-theme-name", "").lower():
                return True
        except Exception:
            pass
    return False


def resolve_mode(mode):
    return ("dark" if system_prefers_dark() else "light") if mode == "auto" else mode


def builtin_theme(mode="light"):
    pal = DARK_PALETTE if mode == "dark" else PALETTE
    t = {k: QColor(*v) for k, v in pal.items()}
    t["dark"] = mode == "dark"
    return t


def apply_palette(app, T):
    pal = QPalette(T["button"], T["window"])
    for role, c in [(QPalette.Base, T["base"]), (QPalette.AlternateBase, T["alt"]),
                    (QPalette.Text, T["text"]), (QPalette.WindowText, T["wtext"]),
                    (QPalette.ButtonText, T["wtext"]), (QPalette.Highlight, T["sel"]),
                    (QPalette.HighlightedText, T["seltext"]),
                    (QPalette.ToolTipBase, T["tip"]), (QPalette.ToolTipText, T["text"]),
                    (QPalette.PlaceholderText, T["inactive"]),
                    (QPalette.BrightText, T["seltext"]), (QPalette.Link, T["sel"].lighter(150 if T["dark"] else 100))]:
        pal.setColor(role, c)
    dis = QColor(T["inactive"])
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, dis)
    app.setPalette(pal)


def system_icon_theme():
    """The desktop's icon theme (KDE or GTK setting), if it can be found."""
    home = Path.home() / ".config"
    for f, sec, key in ((home / "kdeglobals", "Icons", "Theme"),
                        (home / "gtk-3.0/settings.ini", "Settings", "gtk-icon-theme-name"),
                        (home / "gtk-4.0/settings.ini", "Settings", "gtk-icon-theme-name")):
        try:
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.optionxform = str
            cfg.read(f)
            val = cfg[sec][key].strip()
            if val:
                return val
        except Exception:
            pass
    return ""


def apply_theme(app, T):
    from PySide6.QtGui import QFontDatabase
    app.setStyle(QStyleFactory.create("Windows"))
    apply_palette(app, T)
    fonts = DATA / "fonts"
    if fonts.is_dir():
        for f in sorted(fonts.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                QFontDatabase.addApplicationFont(str(f))
    have = set(QFontDatabase.families())
    family = next((f for f in FONT_FAMILIES if f in have), None)
    font = QFont(family, FONT_SIZE) if family else QFontDatabase.systemFont(QFontDatabase.FixedFont)
    T["font"] = font
    app.setFont(font)
    QIcon.setThemeSearchPaths([str(Path.home() / ".local/share/icons"), "/usr/share/icons"]
                              + QIcon.themeSearchPaths())
    theme = system_icon_theme()
    if theme:
        QIcon.setThemeName(theme)
    QIcon.setFallbackThemeName("hicolor")


# freedesktop icon name -> Qt's built-in icon, used when the icon theme lacks one
STD_ICONS = {
    "document-open": "SP_DialogOpenButton", "document-save-as": "SP_DialogSaveButton",
    "document-save": "SP_DialogSaveButton", "media-floppy": "SP_DialogSaveButton",
    "media-playback-start": "SP_MediaPlay", "media-playback-pause": "SP_MediaPause",
    "media-playback-stop": "SP_MediaStop", "media-skip-backward": "SP_MediaSkipBackward",
    "media-skip-forward": "SP_MediaSkipForward", "media-seek-forward": "SP_MediaSeekForward",
    "edit-undo": "SP_ArrowBack", "edit-redo": "SP_ArrowForward", "view-refresh": "SP_BrowserReload",
    "edit-find": "SP_FileDialogContentsView", "system-search": "SP_FileDialogContentsView",
    "folder": "SP_DirIcon", "folder-open": "SP_DirOpenIcon", "folder-music": "SP_DirIcon",
    "media-optical-audio": "SP_DriveCDIcon", "media-optical": "SP_DriveCDIcon",
    "audio-x-generic": "SP_MediaVolume", "dialog-information": "SP_MessageBoxInformation",
    "dialog-warning": "SP_MessageBoxWarning", "dialog-error": "SP_MessageBoxCritical",
    "dialog-ok-apply": "SP_DialogApplyButton", "process-stop": "SP_BrowserStop",
    "dialog-cancel": "SP_DialogCancelButton", "edit-delete": "SP_TrashIcon",
    "list-remove": "SP_TrashIcon", "application-exit": "SP_DialogCloseButton",
    "help-contents": "SP_DialogHelpButton", "help-about": "SP_MessageBoxQuestion",
    "download": "SP_ArrowDown", "go-down": "SP_ArrowDown", "applications-internet": "SP_DriveNetIcon",
    "image-x-generic": "SP_FileIcon", "insert-image": "SP_FileIcon", "edit-paste": "SP_FileDialogDetailedView",
    "document-open-recent": "SP_FileDialogBack", "list-add": "SP_FileDialogNewFolder",
}


def icon(*names):
    for n in names:
        if QIcon.hasThemeIcon(n):
            return QIcon.fromTheme(n)
    style = QApplication.style()
    for n in names:
        sp = getattr(QStyle.StandardPixmap, STD_ICONS.get(n, ""), None) if n in STD_ICONS else None
        if sp is not None and style:
            return style.standardIcon(sp)
    return QIcon()


# ───────────────────────────── data model ─────────────────────────────

class Album(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.path = None
        self.total = 0.0
        self.samples = None   # int16, SR Hz mono
        self.env = None       # 0..1 (dB scaled), 100/s
        self.env_db = None
        self.tracks = []      # [[start, title|None, artist|None]]
        self.candidates = []  # [(a, b)]
        self.disp = (-60.0, 0.0)  # dB range shown in the waveform
        self.name_queue = None    # (titles, artists): names follow the cuts in order until edited
        self.undo_stack, self.redo_stack = [], []

    # tracks
    def title(self, i):
        return self.tracks[i][1] or f"Track {i + 1:02d}"

    def artist(self, i):
        return self.tracks[i][2]

    def label(self, i):
        a = self.artist(i)
        return f"{a} – {self.title(i)}" if a else self.title(i)

    def has_artists(self):
        return any(t[2] for t in self.tracks)

    def end(self, i):
        return self.tracks[i + 1][0] if i + 1 < len(self.tracks) else self.total

    def track_at(self, t):
        for i in range(len(self.tracks) - 1, -1, -1):
            if t >= self.tracks[i][0]:
                return i
        return 0

    def gap_at(self, t):
        for a, b in self.candidates:
            if a - 0.05 <= t <= b + 0.05:
                return b - a
        return None

    def snap(self, t):
        for a, b in self.candidates:
            if a <= t <= b:
                return (a + b) / 2
        return t

    # undo
    def snapshot(self):
        return [list(x) for x in self.tracks]

    def push_undo(self, snap=None):
        self.undo_stack.append(snap if snap is not None else self.snapshot())
        self.undo_stack = self.undo_stack[-300:]
        self.redo_stack.clear()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.snapshot())
            self.tracks = self.undo_stack.pop()
            self.changed.emit()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.snapshot())
            self.tracks = self.redo_stack.pop()
            self.changed.emit()

    # editing
    def add_cut(self, t):
        if not (0.2 < t < self.total - 0.2) or any(abs(t - x[0]) < 0.2 for x in self.tracks):
            return None
        self.push_undo()
        i = self.track_at(t) + 1
        self.tracks.insert(i, [t, None, None])
        self.changed.emit()
        return i

    def remove_cuts(self, idx):
        idx = sorted({i for i in idx if i > 0}, reverse=True)
        if not idx:
            return
        self.push_undo()
        for i in idx:
            del self.tracks[i]
        self.changed.emit()

    def move_cut(self, i, t, emit=True):
        lo, hi = self.tracks[i - 1][0] + 0.1, self.end(i) - 0.1
        self.tracks[i][0] = min(max(t, lo), hi)
        if emit:
            self.changed.emit()

    def set_tracks(self, starts, titles=None, artists=None):
        self.push_undo()
        titles = titles if titles is not None else [x[1] for x in self.tracks]
        artists = artists if artists is not None else [x[2] for x in self.tracks]
        starts = [0.0] + sorted({round(float(s), 3) for s in starts if 0 < s < self.total - 0.2})
        self.tracks = [[s, titles[i] if i < len(titles) else None,
                        artists[i] if i < len(artists) else None] for i, s in enumerate(starts)]
        self.changed.emit()

    def set_names(self, titles, artists):
        self.push_undo()
        for i, tr in enumerate(self.tracks):
            if i < len(titles):
                tr[1] = titles[i]
                tr[2] = artists[i]
        self.changed.emit()

    # detection
    def detect(self, thresh_db, min_len):
        if self.env_db is None:
            self.candidates = []
            return
        silent = (self.env_db < thresh_db).astype(np.int8)
        d = np.diff(np.concatenate(([0], silent, [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        keep = (ends - starts) >= int(round(min_len * 100))
        res = []
        for s, e in zip(starts[keep] / 100.0, ends[keep] / 100.0):
            mid = (s + e) / 2
            if EDGE < mid < self.total - EDGE:
                res.append((float(s), float(e)))
        self.candidates = res

    def detect_auto(self, n_tracks, thresh_db, min_len):
        """Loosen thresholds until at least n_tracks-1 gaps are found."""
        for db, mn in [(thresh_db, min_len), (thresh_db, 0.5), (thresh_db + 5, 0.5),
                       (thresh_db + 5, 0.3), (thresh_db + 10, 0.3), (thresh_db + 15, 0.2)]:
            self.detect(db, mn)
            if len(self.candidates) >= n_tracks - 1:
                return db, mn
        return db, mn

    def apply_detection(self, n_tracks, titles=None, artists=None):
        c = self.candidates
        if n_tracks:
            c = sorted(c, key=lambda x: x[0] - x[1])[: n_tracks - 1]
        self.set_tracks([(a + b) / 2 for a, b in c], titles, artists)

    # analyzer
    def spectrum(self, pos, bands=18):
        if self.samples is None:
            return np.zeros(bands)
        i = int(pos * SR)
        seg = self.samples[i:i + 512].astype(np.float32)
        if seg.size < 512:
            return np.zeros(bands)
        mag = np.abs(np.fft.rfft(seg * np.hanning(512)))[1:]
        edges = np.geomspace(1, mag.size, bands + 1).astype(int)
        lv = np.array([mag[edges[k]:max(edges[k + 1], edges[k] + 1)].max() for k in range(bands)])
        db = 20 * np.log10(lv / (128 * 32768) + 1e-9)
        return np.clip((db + 55) / 55, 0, 1)


# ───────────────────────────── threads ─────────────────────────────

class Loader(QThread):
    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            p = str(self.path)
            info = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,bit_rate:format_tags:stream=codec_type,codec_name:stream_tags",
                 "-of", "json", p], capture_output=True, check=True, **UTF8, **NOWIN).stdout)
            total = float(info["format"]["duration"])
            tags = {k.lower(): v for k, v in info["format"].get("tags", {}).items()}
            streams = info.get("streams", [])
            for s in streams:
                for k, v in s.get("tags", {}).items():
                    tags.setdefault(k.lower(), v)
            raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", p, "-vn", "-ac", "1",
                                  "-ar", str(SR), "-f", "s16le", "-"],
                                 capture_output=True, check=True, **NOWIN).stdout
            samples = np.frombuffer(raw, dtype=np.int16)
            n = samples.size // BLK
            peaks = np.abs(samples[:n * BLK].reshape(n, BLK).astype(np.int32)).max(1) / 32768.0
            env_db = (20 * np.log10(peaks + 1e-6)).astype(np.float32)
            env = np.clip((env_db + 60) / 60, 0, 1).astype(np.float32)
            cover, cover_src = b"", "From file"
            if any(s.get("codec_type") == "video" for s in streams):
                cover = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", p, "-an", "-frames:v", "1",
                                        "-c:v", "copy", "-f", "image2pipe", "-"],
                                       capture_output=True, **NOWIN).stdout
            if not cover:
                for f in sorted(self.path.parent.iterdir()):
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png") and re.search(
                            r"cover|folder|front|" + re.escape(self.path.stem[:20]), f.stem, re.I):
                        cover, cover_src = f.read_bytes(), f.name
                        break
            codec = next((s.get("codec_name", "") for s in streams
                          if s.get("codec_type") == "audio"), "")
            br = int(info["format"].get("bit_rate") or 0) // 1000
            self.done.emit(dict(total=total, tags=tags, samples=samples, env=env,
                                env_db=env_db, cover=cover, cover_src=cover_src, codec=codec, bitrate=br))
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            self.failed.emit("Can't read this file. It may not be audio, or it may be damaged.",
                             err.strip())
        except Exception as e:
            self.failed.emit("Can't read this file.", str(e))


class Exporter(QThread):
    progress = Signal(int, str, bool, str)

    def __init__(self, src, jobs, acodec, meta, cover_path=None, total=None):
        super().__init__()
        self.src, self.jobs, self.acodec, self.meta = src, jobs, acodec, meta
        self.cover_path = cover_path
        self.total = total or len(jobs)
        self.stop = False

    def run(self):
        n = len(self.jobs)
        for k, (s, e, title, artist, out, num) in enumerate(self.jobs):
            if self.stop:
                break
            inputs = ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(self.src)]
            tags = ["-map_metadata", "0", "-metadata", f"title={title}",
                    "-metadata", f"track={num}/{self.total}"]
            meta = dict(self.meta)
            if artist:
                meta["artist"] = artist
            for key, val in meta.items():
                if val:
                    tags += ["-metadata", f"{key}={val}"]
            if out.suffix.lower() == ".mp3":
                tags += ["-id3v2_version", "3"]
            acodec = ["-c:a", "copy"] if self.acodec is None else self.acodec
            if self.acodec is None and out.suffix.lower() == ".flac":
                # A stream-copied FLAC keeps the source's header, so players would show the length of the
                # whole recording. Re-encoding FLAC is lossless (same samples, same bit depth) and fixes it.
                acodec = ["-c:a", "flac"]
            head = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            ok = False
            if self.cover_path and out.suffix.lower() not in (".opus", ".ogg"):
                ok = subprocess.run(head + inputs + ["-i", str(self.cover_path), "-map", "0:a",
                                                     "-map", "1:v", "-c:v", "copy",
                                                     "-disposition:v", "attached_pic"]
                                    + acodec + tags + [str(out)],
                                    stdin=subprocess.DEVNULL, capture_output=True, **NOWIN).returncode == 0
            err = ""
            if not ok:
                r = subprocess.run(head + inputs + ["-map", "0:a"] + acodec + tags + [str(out)],
                                   stdin=subprocess.DEVNULL, capture_output=True, **NOWIN)
                ok = r.returncode == 0
                err = r.stderr.decode(errors="replace").strip().splitlines()[-1:] if not ok else []
                err = err[0] if err else ""
            if ok and self.cover_path and out.suffix.lower() in (".opus", ".ogg"):
                embed_ogg_cover(out, self.cover_path)
            self.progress.emit(k + 1, out.name, ok, err)


def embed_ogg_cover(path, cover_path):
    """ffmpeg can't put pictures into Ogg/Opus – do it with mutagen if available."""
    try:
        import base64
        from mutagen import File
        from mutagen.flac import Picture
        data = Path(cover_path).read_bytes()
        img = QImage.fromData(data)   # not QPixmap: this runs in the export thread
        pic = Picture()
        pic.type, pic.data = 3, data
        pic.mime = "image/png" if image_ext(data) == ".png" else "image/jpeg"
        pic.width, pic.height, pic.depth = img.width(), img.height(), 24
        f = File(str(path))
        f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        f.save()
    except Exception:
        pass


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def image_ext(data):
    return ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"


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


# ───────────────────────────── widgets ─────────────────────────────

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
            self.dropped.emit(Main.to_jpeg(md.imageData()), "dropped image")
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


# ───────────────────────────── tracklist import dialog ─────────────────────────────

class ImportDialog(QDialog):
    """Paste a tracklist from anywhere, pick the layout, preview, apply."""

    def __init__(self, parent, album, text="", standalone=False):
        super().__init__(parent)
        self.album = album if not standalone else Album()
        self.album_neg = QColor(PALETTE["neg"][0], PALETTE["neg"][1], PALETTE["neg"][2]).darker(130)
        self.entries, self.info = [], {}
        self.manual_order = False
        self.setWindowTitle("Import tracklist")
        self.setWindowIcon(icon("edit-paste"))
        self.resize(1150, 640)
        v = QVBoxLayout(self)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Tracklist:"))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "Paste a tracklist here. Lines like these work:\n\n"
            "1. Artist - Title  (3:41)\n"
            "02) Title – Artist 4:05\n"
            "A3 Title\n"
            "00:00 Title\n"
            "[12:34] Artist - Title")
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        lv.addWidget(self.edit, 1)
        bp = QPushButton(icon("edit-paste"), " Paste from clipboard")
        bp.clicked.connect(lambda: self.edit.setPlainText(QApplication.clipboard().text()))
        lv.addWidget(bp)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Preview:"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Artist", "Title", "Start", "Length"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 200)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 80)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(QFontMetrics(self.font()).height() + 4)
        rv.addWidget(self.table, 1)
        split.addWidget(right)
        split.setSizes([480, 670])
        v.addWidget(split, 1)

        # options
        opts = QGroupBox("Layout")
        g = QGridLayout(opts)
        g.addWidget(QLabel("Order:"), 0, 0)
        self.order = QButtonGroup(self)
        orow = QHBoxLayout()
        for i, txt in enumerate(["Title only", "Artist – Title", "Title – Artist"]):
            rb = QRadioButton(txt)
            self.order.addButton(rb, i)
            orow.addWidget(rb)
        orow.addStretch()
        self.order.button(ORDER_ARTIST_TITLE).setChecked(True)
        self.order.idClicked.connect(self._order_clicked)
        g.addLayout(orow, 0, 1, 1, 3)
        g.addWidget(QLabel("Separator:"), 1, 0)
        self.sep = QComboBox()
        for label, _ in SEPARATORS:
            self.sep.addItem(label)
        g.addWidget(self.sep, 1, 1)
        g.addWidget(QLabel("Timestamps:"), 1, 2)
        self.times = QComboBox()
        self.times.addItems(["Auto-detect", "Start times", "Track lengths", "Ignore"])
        g.addWidget(self.times, 1, 3)
        self.junk = QCheckBox("Remove “(Official Video)”, “[HD]” etc.", checked=True)
        self.snap = QCheckBox("Snap cuts to a gap within 3 s", checked=True)
        g.addWidget(self.junk, 2, 1)
        g.addWidget(self.snap, 2, 2, 1, 2)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        v.addWidget(opts)

        self.status = QLabel()
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        self.b_names = QPushButton(icon("edit-rename", "document-edit"), " Rename only")
        self.b_cuts = QPushButton(icon("edit-cut"), " Rename and re-cut")
        self.b_names.setToolTip("Names the tracks in order. Cuts stay where they are.")
        self.b_cuts.setToolTip("Names the tracks and moves the cuts.\n"
                               "With timestamps: cuts go there (moved to a gap within 3 s if snapping is on).\n"
                               "Without: cuts go at the biggest gaps.")
        cancel = QPushButton("Cancel")
        self.b_names.clicked.connect(lambda: self.apply(False))
        self.b_cuts.clicked.connect(lambda: self.apply(True))
        cancel.clicked.connect(self.reject)
        self.b_cuts.setDefault(True)
        for b in (self.b_names, self.b_cuts, cancel):
            row.addWidget(b)
        self.standalone = standalone
        if standalone:
            self.b_names.hide()
            self.snap.hide()
            self.b_cuts.setText(" Use this tracklist")
            self.b_cuts.setToolTip("")
        v.addLayout(row)

        self.edit.textChanged.connect(self.refresh)
        self.sep.currentIndexChanged.connect(self.refresh)
        self.times.currentIndexChanged.connect(self.refresh)
        self.junk.toggled.connect(self.refresh)
        self.edit.setPlainText(text)
        self.refresh()

    def _order_clicked(self, _):
        self.manual_order = True
        self.refresh()

    def refresh(self):
        sep = SEPARATORS[self.sep.currentIndex()][1]
        order = self.order.checkedId() if self.manual_order else None
        self.entries, self.info = parse_tracklist(self.edit.toPlainText(), order, sep,
                                                  self.times.currentIndex(), self.junk.isChecked())
        if not self.manual_order:
            self.order.blockSignals(True)
            self.order.button(self.info["order"]).setChecked(True)
            self.order.blockSignals(False)
        t = self.table
        t.setRowCount(len(self.entries))
        show_artist = self.info["order"] != ORDER_TITLE
        t.setColumnHidden(1, not show_artist)
        for r, e in enumerate(self.entries):
            vals = [e["num"] or str(r + 1), e["artist"] or "", e["title"] or f"Track {r + 1:02d}",
                    fmt(e["start"], 0) if e["start"] is not None else "",
                    fmt(e["length"], 0) if e["length"] is not None else ""]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c in (0, 3, 4):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                t.setItem(r, c, it)
        n = len(self.entries)
        parts = [plural(n, "track")]
        warn = False
        tm = self.info.get("times")
        parts.append({TIMES_START: "start times found", TIMES_LENGTH: "track lengths found"}
                     .get(tm, "no timestamps"))
        if self.info.get("sep") and show_artist:
            parts.append(f"separator “{self.info['sep'].strip() or 'Tab'}”")
        if self.album.path:
            have = len(self.album.tracks)
            if n != have:
                parts.append(f"⚠ file has {plural(have, 'track')} now")
                warn = True
            if n and tm is None:
                parts.append(f"re-cut uses the {plural(n - 1, 'biggest gap')}")
            elif tm in (TIMES_START, TIMES_LENGTH) and self.entries:
                es = self.entries
                end = (es[-1]["start"] or 0) + (es[-1]["length"] or 0)
                if (es[-1]["start"] or 0) > self.album.total:
                    parts.append("⚠ timestamps go past the end of the file")
                    warn = True
                elif tm == TIMES_LENGTH and abs(end - self.album.total) > 30:
                    parts.append(f"⚠ lengths total {fmt(end, 0)}, file is {fmt(self.album.total, 0)}")
                    warn = True
        neg = self.album_neg.name()
        self.status.setText("  ·  ".join(f'<span style="color:{neg}">{x}</span>' if x.startswith("⚠") else x
                                         for x in parts))
        if not getattr(self, "standalone", False):
            self.b_cuts.setDefault(not warn)
            self.b_names.setDefault(warn)
        self.b_names.setEnabled(n > 0)
        self.b_cuts.setEnabled(n > 0 and (self.album.env is not None or self.standalone))

    def apply(self, cuts):
        self.cuts = cuts
        self.accept()


# ───────────────────────────── main window ─────────────────────────────

# ───────────────────────────── YouTube import ─────────────────────────────

def yt_thumb_urls(info):
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    thumbs.sort(key=lambda t: ("maxresdefault" in t["url"], t["url"].split("?")[0].endswith(".jpg"),
                               t.get("width") or 0, t.get("preference") or -99), reverse=True)
    urls = [t["url"] for t in thumbs[:4]]
    if info.get("thumbnail"):
        urls.append(info["thumbnail"])
    return urls


class YtInfo(QThread):
    done = Signal(object, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            cmd = yt_dlp_cmd()
            if not cmd:
                raise FileNotFoundError
            r = subprocess.run(cmd + ["-J", "--no-playlist", "--no-warnings", self.url],
                               capture_output=True, timeout=90, stdin=subprocess.DEVNULL, **UTF8, **NOWIN)
            if r.returncode:
                err = (r.stderr.strip().splitlines() or ["unknown error"])[-1]
                self.done.emit(None, err.replace("ERROR: ", ""))
                return
            info = json.loads(r.stdout)
            info["_thumb"] = b""
            for u in yt_thumb_urls(info):
                try:
                    data = http_get(u, 10)
                    if not QImage.fromData(data).isNull():
                        info["_thumb"] = data
                        break
                except Exception:
                    pass
            self.done.emit(info, "")
        except FileNotFoundError:
            self.done.emit(None, "yt-dlp is not installed.")
        except subprocess.TimeoutExpired:
            self.done.emit(None, "YouTube did not respond. Try again.")
        except Exception as e:
            self.done.emit(None, str(e))


class YtDownload(QThread):
    progress = Signal(float, str)
    done = Signal(str, str)

    def __init__(self, url, folder, mp3):
        super().__init__()
        self.url, self.folder, self.mp3 = url, Path(folder), mp3
        self.proc = None
        self.cancelled = False

    def run(self):
        cmd = (yt_dlp_cmd() or ["yt-dlp"]) + ["--no-playlist", "--newline", "--progress", "-f", "bestaudio/best", "-x",
               "-o", str(self.folder / "%(title)s [%(id)s].%(ext)s"), "--print", "after_move:filepath"]
        if self.mp3:
            cmd += ["--audio-format", "mp3", "--audio-quality", "0"]
        cmd.append(self.url)
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, bufsize=1, **UTF8, **NOWIN)
        except FileNotFoundError:
            self.done.emit("", "yt-dlp is not installed")
            return
        path, last = "", ""
        for line in self.proc.stdout:
            line = line.strip()
            m = re.search(r"\[download\]\s+([\d.]+)%(.*)", line)
            if m:
                rest = re.sub(r"\s+", " ", m.group(2)).strip()
                self.progress.emit(float(m.group(1)), rest)
            elif line.startswith("[ExtractAudio]"):
                self.progress.emit(-1.0, "Converting…")
            elif Path(line).is_absolute() and Path(line).exists():
                path = line
            elif line:
                last = line
        self.proc.wait()
        if self.cancelled:
            for f in self.folder.glob("*.part"):
                try:
                    if time.time() - f.stat().st_mtime < 3600:
                        f.unlink()
                except OSError:
                    pass
            self.done.emit("", "cancelled")
        else:
            self.done.emit(path, "" if path else last.replace("ERROR: ", "") or "Download failed.")

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class YouTubeDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.info = None
        self.result = None
        self.dl = None
        self.entries, self.src = [], None
        self.setWindowTitle("Import from YouTube")
        self.setWindowIcon(icon("applications-internet", "download"))
        self.resize(760, 470)
        v = QVBoxLayout(self)

        row = QHBoxLayout()
        self.e_url = QLineEdit(placeholderText="Paste a YouTube link (SoundCloud, Bandcamp, Mixcloud work too)")
        self.e_url.textEdited.connect(self.url_edited)
        self.b_look = QPushButton(icon("edit-find", "system-search"), " Look up")
        self.b_look.clicked.connect(self.lookup)
        self.e_url.returnPressed.connect(self.lookup)
        row.addWidget(self.e_url, 1)
        row.addWidget(self.b_look)
        v.addLayout(row)

        box = QGroupBox("Video")
        g = QGridLayout(box)
        self.thumb = QLabel(alignment=Qt.AlignCenter)
        self.thumb.setFixedSize(256, 144)
        self.thumb.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        self.l_title = QLabel("–", wordWrap=True)
        f = QFont(self.font())
        f.setBold(True)
        self.l_title.setFont(f)
        self.l_meta = QLabel("", wordWrap=True)
        self.l_tracks = QLabel("", wordWrap=True)
        self.b_edit = QPushButton(icon("document-edit", "edit-rename", "edit-paste"), " Edit tracklist…")
        self.b_edit.setToolTip("Check or fix the tracklist, or paste your own")
        self.b_edit.setEnabled(False)
        self.b_edit.clicked.connect(self.edit_tracklist)
        trow = QHBoxLayout()
        trow.addWidget(self.l_tracks, 1)
        trow.addWidget(self.b_edit)
        g.addWidget(self.thumb, 0, 0, 3, 1)
        g.addWidget(self.l_title, 0, 1)
        g.addWidget(self.l_meta, 1, 1)
        g.addLayout(trow, 2, 1)
        g.setColumnStretch(1, 1)
        v.addWidget(box)

        box = QGroupBox("Download")
        g = QGridLayout(box)
        self.e_dir = QLineEdit(settings.value("yt_dir", "") or str(
            next((d for d in (Path.home() / "Downloads", Path.home() / "Music") if d.is_dir()), Path.home())))
        bd = QToolButton(text="…")
        bd.setToolTip("Choose folder")
        bd.clicked.connect(self.choose_dir)
        self.cb_fmt = QComboBox()
        self.cb_fmt.addItems(["Original (no re-encoding)", "MP3 320 kbit/s"])
        self.cb_fmt.setToolTip("Original keeps YouTube's own audio (usually Opus) without re-encoding.")
        self.c_tracks = QCheckBox("Cut and name from chapters or tracklist", checked=True)
        self.c_cover = QCheckBox("Use the thumbnail as cover", checked=True)
        g.addWidget(QLabel("Save to:"), 0, 0)
        g.addWidget(self.e_dir, 0, 1)
        g.addWidget(bd, 0, 2)
        g.addWidget(QLabel("Format:"), 1, 0)
        g.addWidget(self.cb_fmt, 1, 1, 1, 2)
        g.addWidget(self.c_tracks, 2, 1, 1, 2)
        g.addWidget(self.c_cover, 3, 1, 1, 2)
        g.setColumnStretch(1, 1)
        v.addWidget(box)

        self.bar = QProgressBar()
        self.bar.hide()
        self.status = QLabel("")
        v.addWidget(self.bar)
        v.addWidget(self.status)
        v.addStretch(1)
        row = QHBoxLayout()
        row.addStretch()
        self.b_go = QPushButton(icon("download", "go-down"), " Download and open")
        self.b_go.setDefault(True)
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(self.download)
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.clicked.connect(self.cancel)
        row.addWidget(self.b_go)
        row.addWidget(self.b_cancel)
        v.addLayout(row)

        clip = QApplication.clipboard().text().strip()
        if re.match(r"https?://([\w-]+\.)?(youtube\.com|youtu\.be|soundcloud\.com|bandcamp\.com|mixcloud\.com)/", clip):
            self.e_url.setText(clip)
            QTimer.singleShot(0, self.lookup)

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Save downloads to", self.e_dir.text())
        if d:
            self.e_dir.setText(d)

    def url_edited(self):
        self.info = None
        self.b_go.setEnabled(False)

    def reject(self):
        if self.dl and self.dl.isRunning():
            self.cancel()
            return
        super().reject()

    def lookup(self):
        url = self.e_url.text().strip()
        if not url or (getattr(self, "worker", None) and self.worker.isRunning()):
            return
        self.b_look.setEnabled(False)
        self.b_go.setEnabled(False)
        self.status.setText("Looking up…")
        self.worker = YtInfo(url)
        self.worker.done.connect(self.got_info)
        keep(self.worker)

    def got_info(self, info, err):
        self.b_look.setEnabled(True)
        if err:
            self.status.setText(f"⚠ {err}")
            return
        if info.get("_type") == "playlist":
            self.status.setText("⚠ This is a playlist. Paste the link of a single video.")
            return
        self.info = info
        pm = QPixmap()
        if info.get("_thumb") and pm.loadFromData(info["_thumb"]):
            self.thumb.setPixmap(pm.scaled(256, 144, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.l_title.setText(info.get("title", "?"))
        meta = [info.get("channel") or info.get("uploader") or "", fmt(info.get("duration") or 0, 0)]
        self.l_meta.setText("  ·  ".join(x for x in meta if x))
        self.entries, src = yt_entries(info)
        self.src = src
        self.show_entries()
        self.c_tracks.setEnabled(True)
        self.c_tracks.setChecked(bool(self.entries))
        self.b_edit.setEnabled(True)
        self.c_cover.setEnabled(bool(info.get("_thumb")))
        self.status.setText("")
        self.b_go.setEnabled(True)

    def show_entries(self):
        n = len(self.entries)
        if not n:
            self.l_tracks.setText("No chapters or tracklist found. Cuts will go at gaps.")
        elif self.src == "chapters":
            self.l_tracks.setText(f"✔ {plural(n, 'chapter')}, used for cuts and names")
        elif self.src == "description":
            self.l_tracks.setText(f"✔ Tracklist in description: {plural(n, 'track')} with times")
        elif self.src == "description-untimed":
            self.l_tracks.setText(f"✔ Tracklist in description: {plural(n, 'track')}, no times.\n"
                                  "Cuts go at gaps. Names follow in order.")
        else:
            starts = all(e["start"] is not None for e in self.entries)
            self.l_tracks.setText(f"✔ Your tracklist: {plural(n, 'track')}" + ("" if starts else ", no times"))

    def edit_tracklist(self):
        info = self.info or {}
        if self.entries:
            text = "\n".join(
                (f"{fmt(e['start'], 0)} " if e["start"] is not None else "") +
                (f"{e['artist']} - " if e["artist"] else "") + (e["title"] or "") for e in self.entries)
        else:
            text = info.get("description", "")
        dlg = ImportDialog(self, self.parent().album, text, standalone=True)
        if dlg.exec() == QDialog.Accepted:
            self.entries, self.src = dlg.entries, "edited"
            self.c_tracks.setChecked(bool(self.entries))
            self.show_entries()

    def download(self):
        folder = Path(self.e_dir.text()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.status.setText(f"⚠ Can't use that folder: {e.strerror}")
            return
        self.settings.setValue("yt_dir", str(folder))
        self.b_go.setEnabled(False)
        self.b_look.setEnabled(False)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText("Downloading …")
        self.dl = YtDownload(self.info.get("webpage_url") or self.e_url.text().strip(), folder,
                             self.cb_fmt.currentIndex() == 1)
        self.dl.progress.connect(self.dl_progress)
        self.dl.done.connect(self.finished_dl)
        keep(self.dl)

    def dl_progress(self, pct, text):
        if pct < 0:
            self.bar.setRange(0, 0)
            self.status.setText(text)
        else:
            self.bar.setValue(int(pct * 10))
            self.status.setText(f"Downloading… {text}")

    def finished_dl(self, path, err):
        if err:
            self.bar.hide()
            self.status.setText("" if err == "cancelled" else f"⚠ {err}")
            self.b_go.setEnabled(True)
            self.b_look.setEnabled(True)
            if err == "cancelled":
                QDialog.reject(self)
            elif "yt-dlp" not in err:
                self.status.setText(f"⚠ {err}  (Updating yt-dlp often fixes download errors.)")
            return
        info = self.info
        artist, album, year = guess_from_filename(info.get("title", ""))
        if not artist:
            artist = info.get("artist") or re.sub(r"\s*-\s*Topic$", "", info.get("channel") or "")
        year = str(info.get("release_year") or year or "")
        self.result = dict(path=path, artist=artist, album=album or info.get("title", ""), year=year,
                           source=self.src,
                           entries=self.entries if self.c_tracks.isChecked() else [],
                           thumb=info.get("_thumb") if self.c_cover.isChecked() else b"")
        self.accept()

    def cancel(self):
        if self.dl and self.dl.isRunning():
            self.status.setText("Cancelling …")
            self.dl.cancel()
        else:
            self.reject()

    def closeEvent(self, e):
        if self.dl and self.dl.isRunning():
            self.dl.cancel()
            self.dl.wait(3000)
        super().closeEvent(e)


# ───────────────────────────── cover editor ─────────────────────────────

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
        self.result = Main.to_jpeg(self.render())
        self.accept()


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


GETTING_STARTED = """<b>Cuttycat in three steps</b><ol>
<li><b>Open</b> a long audio file. Cuttycat cuts it at the gaps (red lines).</li>
<li><b>Check the cuts.</b> Double-click a track to play it, press P to hear a cut. Drag a red
line to move it, double-click the waveform to add one, Del removes one. Name the tracks with
<b>Import tracklist</b>, or select one and press F2.</li>
<li><b>Export.</b> Set folder, format and file names on the Export page, then click
<b>Export</b>.</li></ol>
<p>Ctrl+Z undoes any change. Nothing is saved until you export.</p>"""

SHORTCUTS = """<table cellspacing=4>
<tr><td><b>Ctrl+O</b></td><td>Open file</td></tr>
<tr><td><b>Ctrl+Shift+O</b></td><td>Import from YouTube</td></tr>
<tr><td><b>Ctrl+I</b></td><td>Import tracklist</td></tr>
<tr><td><b>Ctrl+E</b></td><td>Export all tracks</td></tr>
<tr><td><b>Ctrl+Shift+E</b></td><td>Export only the selected tracks (also in the right-click menu)</td></tr>
<tr><td><b>Space</b></td><td>Play / pause</td></tr>
<tr><td><b>P</b></td><td>Listen to the selected cut (4 s before and after)</td></tr>
<tr><td><b>C</b></td><td>Add a cut at the play position</td></tr>
<tr><td><b>Del</b></td><td>Remove the cut before the selected track</td></tr>
<tr><td><b>Ctrl+← / Ctrl+→</b></td><td>Previous / next cut</td></tr>
<tr><td><b>Alt+← / Alt+→</b></td><td>Move the selected cut 0.1 s (with Shift: 1 s)</td></tr>
<tr><td><b>Ctrl+R</b></td><td>Cut at gaps (replaces all cuts)</td></tr>
<tr><td><b>F2 / Enter</b></td><td>Rename the selected track</td></tr>
<tr><td><b>Ctrl+Z / Ctrl+Y</b></td><td>Undo / redo</td></tr>
<tr><td><b>+ / − / 0</b></td><td>Zoom in / out / whole file</td></tr>
<tr><td colspan=2><br><b>Waveform</b><br>
Click: jump here · Drag or middle-drag: scroll · Wheel: zoom · Shift+wheel: scroll<br>
Double-click: add cut · Drag red line: move cut · Shift: no snapping to the gap · Right-click: more<br>
Filled band: gap with a cut · Hatched band: gap found but not used</td></tr>
<tr><td colspan=2><br><b>Track list</b><br>
Double-click: play · Right-click: more · Click a selected start time to type an exact time</td></tr></table>"""


class Main(QMainWindow):
    P_OPEN, P_TRACKS, P_EXPORT = range(3)

    def __init__(self, T):
        super().__init__()
        self.T = T
        self.settings = QSettings("cuttycat", "cuttycat")
        self.album = Album()
        self.loader = None
        self.exporter = None
        self.preview_end = None
        self.dirty = False
        self.loading = False
        self.cover = QPixmap()
        self.cover_bytes, self.cover_src, self.embedded_cover = b"", "", b""
        self.last_outdir = Path.home()
        self._cover_tmp = None
        self._after_load = None
        self.exporting = False
        self.load_id = 0
        self.setWindowTitle(APP)
        self.setWindowIcon(QIcon(str(DATA / "icon.png")))
        scr = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400, scr.width() - 40), min(860, scr.height() - 60))
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self.setAcceptDrops(True)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self._pos_ref = (0.0, time.monotonic())
        self.player.positionChanged.connect(lambda ms: setattr(self, "_pos_ref", (ms / 1000, time.monotonic())))
        self.player.playbackStateChanged.connect(
            lambda _: setattr(self, "_pos_ref", (self.player.position() / 1000, time.monotonic())))
        self.player.errorOccurred.connect(
            lambda err, txt: err != QMediaPlayer.Error.NoError and self.msg(f"Can't play audio: {txt}", "error"))
        self.audio.setVolume(float(self.settings.value("volume", 0.5)))

        self._build_actions()
        self._build_menu()
        self._build_ui()

        self.album.changed.connect(self.on_tracks_changed)
        self.timer = QTimer(self, interval=30, timeout=self.tick)
        self.timer.start()
        self.update_enabled()
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(
                lambda _: self.settings.value("theme", "auto") == "auto" and self.set_theme("auto", save=False))
        except Exception:
            pass

    # ── actions & menus ──
    def _act(self, text, icons, slot, key=None, tip=None):
        a = QAction(icon(*icons), text, self)
        a.triggered.connect(slot)
        if key:
            a.setShortcuts([QKeySequence(k) for k in key.split("|")])
        a.setToolTip(tip or text + (f"  ({QKeySequence(key.split('|')[0]).toString()})" if key else ""))
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_open = A("Open file…", ["document-open"], self.choose_file, "Ctrl+O")
        self.a_youtube = A("Import from YouTube…", ["applications-internet", "download"],
                           self.youtube_import, "Ctrl+Shift+O",
                           "Download a video's audio and use its chapters and thumbnail (Ctrl+Shift+O)")
        self.a_export = A("Export tracks…", ["document-save-as", "media-floppy"], self.export_button, "Ctrl+E")
        self.a_export_sel = A("Export selected tracks…", ["document-save-as", "media-floppy"],
                              self.export_selected, "Ctrl+Shift+E",
                              "Export only the tracks selected in the list (Ctrl+Shift+E)")
        self.a_quit = A("Quit", ["application-exit"], self.close, "Ctrl+Q")
        self.a_undo = A("Undo", ["edit-undo"], self.album.undo, "Ctrl+Z")
        self.a_redo = A("Redo", ["edit-redo"], self.album.redo, "Ctrl+Shift+Z|Ctrl+Y")
        self.a_import = A("Import tracklist…", ["edit-paste"], self.import_tracklist, "Ctrl+I",
                          "Name the tracks from a pasted tracklist (Ctrl+I)")
        self.a_cover = A("Change cover…", ["image-x-generic", "insert-image"], self.cover_menu)
        self.a_autocover = A("Look for covers online", ["edit-find"], lambda on: self.settings.setValue("auto_cover", on))
        self.a_autocover.setCheckable(True)
        self.a_autocover.setChecked(self.settings.value("auto_cover", "true") in (True, "true"))
        self.a_autocover.setToolTip("When a file has no cover, search iTunes, Deezer and MusicBrainz for one")
        self.a_detect = A("Cut at gaps", ["view-refresh"], self.apply_detection, "Ctrl+R",
                          "Replace all cuts with cuts at the detected gaps (Ctrl+R)")
        self.a_cut = A("Add cut at play position", ["edit-cut", "list-add"], self.cut_here, "C")
        self.a_del = A("Remove cut", ["list-remove", "edit-delete"], self.delete_cuts, "Del",
                       "Joins the selected track to the one before (Del)")
        self.a_prev = A("Previous cut", ["media-skip-backward"], self.prev_cut, "Ctrl+Left")
        self.a_next = A("Next cut", ["media-skip-forward"], self.next_cut, "Ctrl+Right")
        self.a_nudge_l = A("Move cut left", ["go-previous"], lambda: self.nudge(-0.1), "Alt+Left")
        self.a_nudge_r = A("Move cut right", ["go-next"], lambda: self.nudge(0.1), "Alt+Right")
        self.a_nudge_L = A("Move cut left 1 s", ["go-previous"], lambda: self.nudge(-1), "Alt+Shift+Left")
        self.a_nudge_R = A("Move cut right 1 s", ["go-next"], lambda: self.nudge(1), "Alt+Shift+Right")
        self.a_play = A("Play / pause", ["media-playback-start"], self.play_pause, "Space")
        self.a_stop = A("Stop", ["media-playback-stop"], self.stop)
        self.a_preview = A("Listen to cut", ["media-seek-forward", "media-playback-start"],
                           self.preview_selected, "P",
                           "Play 4 seconds before and after the selected cut (P)")
        self.a_zin = A("Zoom in", ["zoom-in"], lambda: self.wave.zoom(0.5), "+")
        self.a_zout = A("Zoom out", ["zoom-out"], lambda: self.wave.zoom(2), "-")
        self.a_zfit = A("Show whole file", ["zoom-fit-best", "zoom-original"],
                        lambda: self.wave.reset_view(), "0")

    def _build_menu(self):
        mb = self.menuBar()

        def menu(title, items):
            m = mb.addMenu(title)
            for a in items:
                m.addSeparator() if a is None else m.addAction(a)
            return m

        m = menu("&File", [self.a_open, self.a_youtube])
        self.recent_menu = m.addMenu(icon("document-open-recent"), "Open recent")
        self.recent_menu.aboutToShow.connect(self.fill_recent_menu)
        for a in (None, self.a_export, self.a_export_sel, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        menu("&Edit", [self.a_undo, self.a_redo, None, self.a_import, self.a_cover, None, self.a_autocover])
        menu("&Cuts", [self.a_cut, self.a_del, None, self.a_nudge_l, self.a_nudge_r, None,
                       self.a_prev, self.a_next, None, self.a_detect])
        menu("&Play", [self.a_play, self.a_stop, self.a_preview])
        m = menu("&View", [self.a_zin, self.a_zout, self.a_zfit, None])
        tm = m.addMenu("Theme")
        from PySide6.QtGui import QActionGroup
        grp = QActionGroup(self)
        cur = self.settings.value("theme", "auto")
        for key, label in (("auto", "Like the system"), ("light", "Light"), ("dark", "Dark")):
            a = tm.addAction(label, lambda k=key: self.set_theme(k))
            a.setCheckable(True)
            a.setChecked(key == cur)
            grp.addAction(a)
        m = mb.addMenu("&Help")
        m.addAction(icon("help-contents"), "Getting started",
                    lambda: QMessageBox.information(self, "Getting started", GETTING_STARTED))
        m.addAction(icon("preferences-desktop-keyboard", "input-keyboard"), "Keyboard and mouse",
                    lambda: QMessageBox.information(self, "Keyboard and mouse", SHORTCUTS))
        m.addAction(QIcon(str(DATA / "icon.png")), "About Cuttycat",
                    lambda: QMessageBox.about(self, "About Cuttycat",
                                              f"<b>Cuttycat</b> {__version__}<br>Splits long recordings into tracks.<br>"
                                              "Made by Twongo<br><br>"
                                              "License: GNU GPL version 3 or later. Uses ffmpeg and yt-dlp.<br>"
                                              "Covers from iTunes, Deezer, MusicBrainz / Cover Art Archive."))
        m.addSeparator()
        m.addAction(icon("help-donate", "emblem-favorite"), "Buy me a coffee",
                    lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))

    def _build_ui(self):
        T = self.T
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        self.side = SideBar([(icon("folder-music", "folder-sound", "document-open"), "Open"),
                             (icon("media-optical-audio", "media-optical", "audio-x-generic"), "Tracks"),
                             (icon("media-floppy", "document-save-as", "document-save"), "Export")])
        self.side.T = T
        root.addWidget(self.side)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(4)
        root.addWidget(split, 1)

        # left column: page + cover + big export button
        left = self.left = QWidget()
        left.setAutoFillBackground(True)
        lp = left.palette()
        lp.setColor(QPalette.Window, T["base"])
        left.setPalette(lp)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(4, 4, 4, 6)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_open())
        self.stack.addWidget(self._page_tracks())
        self.stack.addWidget(self._page_export())
        self.side.changed.connect(self.stack.setCurrentIndex)
        self.side.changed.connect(lambda i: self.np_cover.setVisible(i != self.P_EXPORT))
        lv.addWidget(self.stack, 1)
        self.np_cover = CoverLabel(180)
        self.np_cover.clicked.connect(self.cover_menu)
        self.np_cover.dropped.connect(self.cover_from_bytes)
        self.np_text = QLabel(alignment=Qt.AlignCenter, wordWrap=True)
        self.np_text.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bold = QFont(T["font"])
        bold.setBold(True)
        self.np_text.setFont(bold)
        lv.addWidget(self.np_cover)
        lv.addWidget(self.np_text)
        self.b_export = QPushButton(icon("document-save-as", "media-floppy"), " Export tracks")
        self.b_export.setFont(bold)
        self.b_export.setMinimumHeight(42)
        self.b_export.setToolTip("Choose a folder and save every track as its own file (Ctrl+E).\n"
                                 "Format, file names and cover: see the Export page.")
        self.b_export.clicked.connect(self.export_button)
        lv.addWidget(self.b_export)
        self.l_dest = QLabel(alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.l_dest.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.l_dest.setStyleSheet(f"color: {T['text'].name()};")
        lv.addWidget(self.l_dest)
        split.addWidget(left)

        # right column
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 2, 0, 0)
        rv.setSpacing(2)
        tb = QToolBar()
        tb.setIconSize(QSize(16, 16))
        tb.setStyleSheet("QToolBar{border:0;}")
        for a in (self.a_open, self.a_youtube, None, self.a_undo, self.a_redo, None, self.a_cut, self.a_del,
                  self.a_preview, None, self.a_import, None, self.a_zin, self.a_zout, self.a_zfit,
                  None, self.a_export):
            tb.addSeparator() if a is None else tb.addAction(a)
        for a, short in ((self.a_open, "Open"), (self.a_youtube, "YouTube"), (self.a_cut, "Cut"),
                         (self.a_del, "Remove"), (self.a_preview, "Listen"),
                         (self.a_import, "Tracklist"), (self.a_export, "Export")):
            a.setIconText(short)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toolbar = tb
        top = QHBoxLayout()
        top.setContentsMargins(2, 0, 6, 0)
        top.addWidget(tb)
        top.addStretch()
        rv.addLayout(top)
        self.msgbar = MsgBar()
        rv.addWidget(self.msgbar)

        wf = QFrame()
        wf.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        wl = QVBoxLayout(wf)
        wl.setContentsMargins(2, 2, 2, 2)
        wl.setSpacing(0)
        self.wave = Waveform(self.album, T)
        self.wave.seek.connect(self.seek)
        self.wave.play_at.connect(lambda t: (self.seek(t), self.player.play()))
        self.wave.preview.connect(self.preview_at)
        self.wave.view_changed.connect(self.sync_scroll)
        self.wave.open_requested.connect(self.choose_file)
        self.hscroll = QScrollBar(Qt.Horizontal)
        self.hscroll.valueChanged.connect(self.scroll_moved)
        self.l_hint = QLabel(Waveform.HINT)
        self.l_hint.setStyleSheet(f"color: {T['wtext'].name()}; padding: 2px 4px;")
        self.wave.hint.connect(self.l_hint.setText)
        self.l_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        wl.addWidget(self.wave, 1)
        wl.addWidget(self.hscroll)
        wl.addWidget(self.l_hint)

        self.table = TrackTable(T)
        self.table.itemChanged.connect(self.table_edited)
        self.table.itemSelectionChanged.connect(self.table_selected)
        self.table.cellDoubleClicked.connect(self.table_double)
        self.table.setEditTriggers(QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
        self.table.setToolTip("Double-click a track to play it  ·  F2 or Enter to rename it")
        self.table.setColumnHidden(TrackTable.C_ARTIST, True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(wf)
        vsplit.addWidget(self.table)
        vsplit.setSizes([320, 400])
        rv.addWidget(vsplit, 1)
        rv.addWidget(self._player_bar())
        split.addWidget(right)
        split.setSizes([370, 1030])
        split.setStretchFactor(1, 1)

    def _group(self, title):
        g = QGroupBox(title)
        lay = QGridLayout(g)
        lay.setContentsMargins(6, 8, 6, 6)
        return g, lay

    def _page_open(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        b = QPushButton(icon("folder-music", "document-open"), " Open file…")
        b.setMinimumHeight(42)
        f = QFont(self.T["font"])
        f.setBold(True)
        b.setFont(f)
        b.clicked.connect(self.choose_file)
        v.addWidget(b)
        self.b_youtube = QPushButton(icon("applications-internet", "download"), " Import from YouTube…")
        self.b_youtube.setMinimumHeight(34)
        self.b_youtube.clicked.connect(self.youtube_import)
        v.addWidget(self.b_youtube)
        hint = QLabel("Or drop a file onto the window. MP3, FLAC, Opus, M4A, WAV and most other formats.")
        hint.setWordWrap(True)
        v.addWidget(hint)
        g, lay = self._group("Recent files")
        self.recent = QListWidget()
        self.recent.setTextElideMode(Qt.ElideMiddle)
        self.recent.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent.setToolTip("Double-click to open · right-click for more")
        self.recent.itemActivated.connect(lambda it: it.data(Qt.UserRole) and self.open_file(Path(it.data(Qt.UserRole))))
        self.recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self.recent_context)
        lay.addWidget(self.recent, 0, 0)
        v.addWidget(g, 1)
        self.fill_recent()
        return w

    def _page_tracks(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)

        g, lay = self._group("Tags")
        self.e_artist = QLineEdit(placeholderText="e.g. Hoodoo Lord")
        self.e_album = QLineEdit(placeholderText="e.g. Timelapse Freefall")
        self.e_year = QLineEdit(placeholderText="e.g. 2026")
        self.e_year.setMaximumWidth(90)
        for r, (lbl, wd, tip) in enumerate((
                ("Artist:", self.e_artist, "Written to every track. Also used for the folder name."),
                ("Album:", self.e_album, "Written to every track. Also used for the folder name and the cover search."),
                ("Year:", self.e_year, "Release year (optional)"))):
            wd.setToolTip(tip)
            lay.addWidget(QLabel(lbl), r, 0)
            lay.addWidget(wd, r, 1)
            wd.textEdited.connect(self.mark_dirty)
        v.addWidget(g)

        g, lay = self._group("Gap detection")
        self.s_db = QSlider(Qt.Horizontal, minimum=-70, maximum=-15, value=-40)
        self.s_db.setToolTip("Anything quieter than this counts as a gap.\n"
                             "Gaps missed: move right. Cuts inside quiet tracks: move left.")
        self.l_db = QLabel()
        self.s_min = QSlider(Qt.Horizontal, minimum=1, maximum=50, value=10)
        self.s_min.setToolTip("Minimum gap length. Lower it if tracks are separated by short gaps.")
        self.l_min = QLabel()
        self.sp_n = QSpinBox(minimum=0, maximum=199, specialValueText="auto")
        self.sp_n.setToolTip("If you know the track count, enter it.\n"
                             "Cuttycat then cuts at the longest gaps to match.")
        self.l_cand = QLabel()
        for wd in (self.s_db, self.s_min):
            wd.valueChanged.connect(self.detect_live)
        self.sp_n.valueChanged.connect(self.detect_live)
        lay.addWidget(QLabel("Silence level:"), 0, 0)
        lay.addWidget(self.s_db, 0, 1)
        lay.addWidget(self.l_db, 0, 2)
        lay.addWidget(QLabel("Shortest gap:"), 1, 0)
        lay.addWidget(self.s_min, 1, 1)
        lay.addWidget(self.l_min, 1, 2)
        lay.addWidget(QLabel("Number of tracks:"), 2, 0)
        lay.addWidget(self.sp_n, 2, 1, 1, 2)
        lay.addWidget(self.l_cand, 3, 0, 1, 3)
        self.b_detect = QPushButton(icon("view-refresh"), " Cut at gaps")
        self.b_detect.setToolTip("Replaces all cuts (Ctrl+R)")
        self.b_detect.clicked.connect(self.apply_detection)
        lay.addWidget(self.b_detect, 4, 0, 1, 3)
        self.l_db.setMinimumWidth(QFontMetrics(self.T["font"]).horizontalAdvance("-70 dB "))
        self.detect_group = g
        v.addWidget(g)

        g, lay = self._group("Track names")
        info = QLabel("Paste a tracklist to name all tracks. To rename one, select it and press F2.")
        info.setWordWrap(True)
        self.b_import = QPushButton(icon("edit-paste"), " Import tracklist…")
        self.b_import.setMinimumHeight(30)
        self.b_import.clicked.connect(self.import_tracklist)
        lay.addWidget(info, 0, 0)
        lay.addWidget(self.b_import, 1, 0)
        v.addWidget(g)
        v.addStretch(1)
        self.detect_live()
        return w

    def _page_export(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)

        g, lay = self._group("Save to")
        self.e_out = QLineEdit()
        self.e_out.textEdited.connect(self.update_example)
        bo = QToolButton(text="…")
        bo.setToolTip("Choose folder")
        bo.clicked.connect(self.choose_outdir)
        self.c_subdir = QCheckBox("Create folder “Artist - Album”", checked=True)
        self.cb_fmt = QComboBox()
        for name, _, _ in FORMATS:
            self.cb_fmt.addItem(name)
        self.cb_fmt.setToolTip("“Original” copies the audio as is: fastest, no quality loss.\n"
                               "Cuts then land on the nearest audio frame (a few ms).")
        lay.addWidget(self.e_out, 0, 0)
        lay.addWidget(bo, 0, 1)
        lay.addWidget(self.c_subdir, 1, 0, 1, 2)
        lay.addWidget(QLabel("Format:"), 2, 0, 1, 2)
        lay.addWidget(self.cb_fmt, 3, 0, 1, 2)
        v.addWidget(g)

        g, lay = self._group("File names")
        self.c_num = QCheckBox("Track number", checked=True)
        self.c_fartist = QCheckBox("Artist")
        self.c_falbum = QCheckBox("Album")
        self.c_ftitle = QCheckBox("Title", checked=True)
        self.cb_sep = QComboBox()
        for label in ("01 - Title", "01. Title", "01 Title", "01_Title"):
            self.cb_sep.addItem(label)
        self.l_example = QLabel()
        self.l_example.setWordWrap(True)
        self.l_example.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.l_example.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.c_num, 0, 0)
        lay.addWidget(self.c_fartist, 0, 1)
        lay.addWidget(self.c_falbum, 1, 0)
        lay.addWidget(self.c_ftitle, 1, 1)
        lay.addWidget(QLabel("Style:"), 2, 0)
        lay.addWidget(self.cb_sep, 2, 1)
        lay.addWidget(self.l_example, 3, 0, 1, 2)
        for wd in (self.c_num, self.c_fartist, self.c_falbum, self.c_ftitle, self.c_subdir):
            wd.toggled.connect(self.update_example)
        self.cb_sep.currentIndexChanged.connect(self.update_example)
        self.cb_fmt.currentIndexChanged.connect(self.update_example)
        v.addWidget(g)

        g, lay = self._group("Cover")
        self.ex_cover = CoverLabel(110)
        self.ex_cover.clicked.connect(self.cover_menu)
        self.ex_cover.dropped.connect(self.cover_from_bytes)
        self.l_cover_src = QLabel("No cover")
        self.l_cover_src.setWordWrap(True)
        bcol = QVBoxLayout()
        for txt, ic, slot in (("Find online…", ("edit-find", "system-search"), self.cover_search),
                              ("Image file…", ("document-open", "image-x-generic"), self.cover_from_file),
                              ("From link…", ("insert-link", "applications-internet"), self.cover_from_url)):
            b = QPushButton(icon(*ic), " " + txt)
            b.clicked.connect(slot)
            bcol.addWidget(b)
        bcol.addStretch()
        lay.addWidget(self.ex_cover, 0, 0, 2, 1)
        lay.addLayout(bcol, 0, 1)
        lay.addWidget(self.l_cover_src, 2, 0, 1, 2)
        self.c_cover = QCheckBox("Embed in every track", checked=True)
        self.c_coverfile = QCheckBox("Also save as cover file in the folder")
        lay.addWidget(self.c_cover, 3, 0, 1, 2)
        lay.addWidget(self.c_coverfile, 4, 0, 1, 2)
        v.addWidget(g)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(120)
        self.log.hide()
        v.addWidget(self.log)
        v.addStretch(1)
        self.load_export_settings()
        self.cb_fmt.currentIndexChanged.connect(self.update_export_caption)
        self.c_subdir.toggled.connect(self.update_export_caption)
        self.e_out.textEdited.connect(self.update_export_caption)
        self.update_example()
        return w

    def _player_bar(self):
        bar = QWidget()
        bar.setAutoFillBackground(True)
        g = QGridLayout(bar)
        g.setContentsMargins(6, 2, 6, 4)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(0)
        btns = QHBoxLayout()
        btns.setSpacing(2)
        for a in (self.a_prev, self.a_play, self.a_stop, self.a_next, self.a_preview):
            b = QToolButton()
            b.setDefaultAction(a)
            b.setIconSize(QSize(22, 22))
            b.setAutoRaise(True)
            btns.addWidget(b)
            if a is self.a_play:
                self.b_play = b
        g.addLayout(btns, 0, 0)
        self.analyzer = Analyzer(self.T)
        g.addWidget(self.analyzer, 0, 1, 1, 3)
        self.vol = VolumeWedge(self.T, self.audio.volume())
        self.vol.changed.connect(self.set_volume)
        g.addWidget(self.vol, 0, 4)

        self.l_status = QLabel()
        self.l_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.l_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.l_status.setMinimumWidth(160)
        self.l_pos = QLabel("0:00")
        self.l_len = QLabel("0:00")
        self.seekbar = JumpSlider(Qt.Horizontal)
        self.seekbar.sliderMoved.connect(self.seekbar_dragged)
        self.seekbar.sliderReleased.connect(lambda: self.seek(self.seekbar.value() / 1000))
        self.seekbar.jumped.connect(lambda v: self.seek(v / 1000))
        g.addWidget(self.l_status, 1, 0)
        g.addWidget(self.l_pos, 1, 1)
        g.addWidget(self.seekbar, 1, 2)
        g.addWidget(self.l_len, 1, 3, 1, 2)
        g.setColumnStretch(2, 1)
        self.update_status()
        return bar

    def seekbar_dragged(self, v):
        self.wave.playhead = v / 1000
        self.wave.ensure_visible(v / 1000)
        self.l_pos.setText(fmt(v / 1000, 0))
        self.wave.update()

    def set_theme(self, mode, save=True):
        if save:
            self.settings.setValue("theme", mode)
        new = builtin_theme(resolve_mode(mode))
        font = self.T.get("font")
        self.T.update(new)
        if font is not None:
            self.T["font"] = font
        apply_palette(QApplication.instance(), self.T)
        lp = self.left.palette()
        lp.setColor(QPalette.Window, self.T["base"])
        self.left.setPalette(lp)
        self.table.refresh_palette()
        self.l_hint.setStyleSheet(f"color: {self.T['wtext'].name()}; padding: 2px 4px;")
        self.l_dest.setStyleSheet(f"color: {self.T['text'].name()};")
        for wd in QApplication.allWidgets():   # widgets with a style sheet cache the old palette
            ss = wd.styleSheet()
            if ss and wd is not self.msgbar:
                wd.setStyleSheet("")
                wd.setStyleSheet(ss)
        MsgBar.dark = self.T["dark"]
        if self.msgbar.isVisible():
            self.msgbar.show_msg(self.msgbar.text.text(), self._last_level, None)
        self.on_tracks_changed() if self.album.path else None
        for wd in (self.side, self.wave, self.analyzer, self.vol, self.table.viewport()):
            wd.update()

    def set_volume(self, v):
        self.audio.setVolume(v)
        self.settings.setValue("volume", v)

    # ── messages / state ──
    def msg(self, text, level="info", ms=None, action=None, busy=False):
        self._last_level = level
        if ms is None and level in ("info", "ok") and not busy and not action:
            ms = 9000
        self.msgbar.show_msg(text, level, ms, action, busy)

    def flash(self, text, ms=None):
        self.msg(text, "info", ms)

    def update_export_caption(self):
        if not hasattr(self, "l_dest") or not hasattr(self, "cb_fmt"):
            return
        if self.album.path:
            fmt_name = FORMATS[self.cb_fmt.currentIndex()][0].split(" (")[0]
            d = str(self.target_dir()).replace(str(Path.home()), "~", 1)
            self.l_dest.setText(f"→ {fmt_name} · {d}")
            self.l_dest.setToolTip(str(self.target_dir()))
        else:
            self.l_dest.setText("")

    def mark_dirty(self, *_):
        if self.album.path and not self.loading:
            self.dirty = True
            self.update_title()

    def update_title(self):
        a = self.album
        self.setWindowTitle(f"{'• ' if self.dirty else ''}{a.path.name} — {APP}" if a.path else APP)

    def confirm_discard(self, what="open another file"):
        if not (self.dirty and self.album.path):
            return True
        r = QMessageBox.question(
            self, APP, f"Changes to “{self.album.path.name}” have not been exported.\n\nDiscard them?",
            QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        return r == QMessageBox.Discard

    def update_enabled(self):
        has = self.album.path is not None and not self.loading
        busy = self.loading or self.exporting
        for wd in (self.detect_group, self.b_import, self.b_export, self.table):
            wd.setEnabled(has)
        for a in (self.a_export, self.a_import, self.a_detect, self.a_cut, self.a_del, self.a_prev,
                  self.a_next, self.a_play, self.a_stop, self.a_preview, self.a_zin, self.a_zout,
                  self.a_zfit, self.a_cover, self.a_undo, self.a_redo):
            a.setEnabled(has)
        self.a_open.setEnabled(not busy)
        self.a_youtube.setEnabled(not busy)
        self.b_youtube.setEnabled(not busy)
        self.a_import.setEnabled(has and not self.exporting)
        self.a_export_sel.setEnabled(has and not self.exporting)
        self.b_import.setEnabled(has and not self.exporting)
        self.a_undo.setEnabled(has and bool(self.album.undo_stack))
        self.a_redo.setEnabled(has and bool(self.album.redo_stack))
        n = len(self.album.tracks)
        if self.exporting:
            self.b_export.setText(" Cancel export")
            self.a_export.setEnabled(False)
        else:
            self.b_export.setText(f" Export {plural(n, 'track')}…" if has else " Export tracks…")
        self.update_export_caption()
        self.b_import.setToolTip("Name the tracks from a pasted tracklist (Ctrl+I)" if has else "Open a file first.")

    # ── recent files ──
    def recent_files(self):
        r = self.settings.value("recent", [])
        r = [r] if isinstance(r, str) else (r or [])
        return [x for x in r if Path(x).is_file()]

    def add_recent(self, path):
        r = [str(path)] + [x for x in self.recent_files() if x != str(path)]
        self.settings.setValue("recent", r[:12])
        self.fill_recent()

    def fill_recent(self):
        self.recent.clear()
        files = self.recent_files()
        for f in files:
            it = QListWidgetItem(icon("audio-x-generic"), Path(f).name)
            it.setData(Qt.UserRole, f)
            it.setToolTip(f)
            self.recent.addItem(it)
        if not files:
            it = QListWidgetItem("No recent files")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)

    def recent_context(self, pos):
        it = self.recent.itemAt(pos)
        if not it or not it.data(Qt.UserRole):
            return
        path = Path(it.data(Qt.UserRole))
        m = QMenu(self)
        m.addAction(icon("document-open"), "Open", lambda: self.open_file(path))
        m.addAction(icon("folder-open", "folder"), "Open file location", lambda: self.show_in_folder(path))
        m.addSeparator()
        m.addAction(icon("list-remove", "edit-clear"), "Remove from list", lambda: self.remove_recent(path))
        m.addAction(icon("user-trash", "edit-delete"), "Move to trash…", lambda: self.trash_file(path))
        m.exec(self.recent.viewport().mapToGlobal(pos))

    def show_in_folder(self, path):
        """Open the file manager with the file selected (falls back to just the folder)."""
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
            return
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusMessage
            msg = QDBusMessage.createMethodCall("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                                "org.freedesktop.FileManager1", "ShowItems")
            msg.setArguments([[QUrl.fromLocalFile(str(path)).toString()], ""])
            reply = QDBusConnection.sessionBus().call(msg)
            if reply.type() != QDBusMessage.ErrorMessage:
                return
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def remove_recent(self, path):
        self.settings.setValue("recent", [x for x in self.recent_files() if x != str(path)])
        self.fill_recent()

    def trash_file(self, path):
        if self.album.path == path:
            self.msg("This file is open. Open another one first.", "warn", 5000)
            return
        if QMessageBox.question(self, APP, f"Move “{path.name}” to the trash?") != QMessageBox.Yes:
            return
        from PySide6.QtCore import QFile
        if QFile.moveToTrash(str(path)):
            self.remove_recent(path)
            self.msg(f"Moved {path.name} to the trash.", "ok", 5000)
        else:
            self.msg(f"Couldn't move {path.name} to the trash.", "error")

    def fill_recent_menu(self):
        self.recent_menu.clear()
        for f in self.recent_files():
            self.recent_menu.addAction(Path(f).name, lambda f=f: self.open_file(Path(f)))
        if not self.recent_files():
            self.recent_menu.addAction("(empty)").setEnabled(False)

    # ── loading ──
    def choose_file(self):
        start = str(self.album.path.parent) if self.album.path else \
            str(Path(self.recent_files()[0]).parent if self.recent_files() else Path.home())
        f, _ = QFileDialog.getOpenFileName(self, "Open file", start,
                                           "Audio (" + " ".join(AUDIO_EXT) + ");;All files (*)")
        if f:
            self.open_file(Path(f))

    def open_file(self, path):
        if self.exporting:
            self.msg("Wait for the export to finish.", "warn", 4000)
            return
        if self.loading:
            self.msg("Still reading the previous file.", "warn", 4000)
            return
        if not path.is_file():
            self.msg(f"File not found: {path}", "error")
            return
        if not self.confirm_discard():
            return
        self.player.stop()
        self.load_id += 1
        self.loading = True
        self.wave.loading_text = f"Reading {path.name} …"
        self.wave.update()
        self.msg(f"Reading {path.name}…", "info", busy=True)
        self.setCursor(Qt.BusyCursor)
        self.update_enabled()
        self.loader = Loader(path)
        self.loader.done.connect(lambda d: self.loaded(path, d))
        self.loader.failed.connect(self.load_failed)
        self.loader.start()

    def load_failed(self, text, details):
        self.loading = False
        self.wave.loading_text = ""
        self.unsetCursor()
        self.msgbar.hide()
        self.update_enabled()
        self.wave.update()
        box = QMessageBox(QMessageBox.Warning, APP, text, QMessageBox.Ok, self)
        if details:
            box.setDetailedText(details)
        box.exec()

    def loaded(self, path, d):
        self.unsetCursor()
        a = self.album
        a.path, a.total = path, d["total"]
        a.samples, a.env_db = d["samples"], d["env_db"]
        hi = float(np.percentile(a.env_db, 99.5)) if a.env_db.size else 0.0
        lo = max(float(np.percentile(a.env_db, 2)), hi - 60)
        if hi - lo < 12:
            lo = hi - 12
        a.disp = (lo, hi)
        a.env = np.clip((a.env_db - lo) / (hi - lo), 0, 1).astype(np.float32)
        a.tracks, a.undo_stack, a.redo_stack = [[0.0, None, None]], [], []
        a.name_queue = None
        tags = d["tags"]
        ga, gb, gy = guess_from_filename(path.stem)
        self.e_artist.setText(tags.get("album_artist") or tags.get("artist") or ga)
        self.e_album.setText(tags.get("album") or gb)
        self.e_year.setText((tags.get("date") or tags.get("year") or gy)[:4])
        self.e_out.setText(self.settings.value("export_dir", "") or str(path.parent))
        info = [path.name, fmt(a.total, 0)]
        if d.get("codec"):
            info.append(d["codec"].upper() + (f" {d['bitrate']} kbit/s" if d.get("bitrate") else ""))
        self.file_info = "  ·  ".join(info[2:])
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.sp_n.blockSignals(True)
        self.sp_n.setValue(0)
        self.sp_n.blockSignals(False)
        self.detect_live()
        self.wave.loading_text = ""
        self.wave.reset_view()
        self.embedded_cover = d["cover"]
        self.set_cover(d["cover"], d.get("cover_src", "From file"))
        if not d["cover"] and not self._after_load and self.a_autocover.isChecked():
            self.auto_cover()
        a.apply_detection(0)
        a.undo_stack.clear()
        self.loading = False
        self.dirty = False
        self.update_title()
        self.update_now_playing()
        self.update_enabled()
        self.add_recent(path)
        self.side.set_current(self.P_TRACKS)
        n = len(a.tracks)
        if self._after_load:
            hook, self._after_load = self._after_load, None
            hook()
            self.dirty = False
            self.update_title()
        elif n <= 1:
            self.msg("No gaps found. Import a tracklist with timestamps, or play and press C "
                     "where a track starts.", "warn",
                     action=("Import tracklist …", self.import_tracklist))
        else:
            self.msg(f"Found {plural(n, 'track')}. Double-click a track to play it, press P to hear a cut.",
                     "ok", 12000)

    def update_now_playing(self):
        self.np_cover.set_image(self.cover)
        txt = " - ".join(x for x in (self.e_artist.text(), self.e_album.text()) if x)
        self.np_text.setText(txt or (self.album.path.name if self.album.path else ""))
        self.update_example()

    # ── cover art ──
    def set_cover(self, data, source):
        pm = QPixmap()
        if data and not pm.loadFromData(data):
            self.msg("Can't read this image.", "error")
            return False
        self.cover, self.cover_bytes, self.cover_src = pm, data or b"", source if data else ""
        self.table.cover = pm
        self.table.viewport().update()
        self.np_cover.set_image(pm)
        self.ex_cover.set_image(pm)
        square = not data or abs(pm.width() - pm.height()) <= 4
        self.l_cover_src.setText((f"{source} · {pm.width()}×{pm.height()}" +
                                  ("" if square else "\nNot square. Click the cover to adjust it.")) if data else "No cover")
        return True

    def cover_menu(self):
        m = QMenu(self)
        m.addAction(icon("edit-find", "system-search"), "Find online…", self.cover_search)
        m.addAction(icon("document-open", "image-x-generic"), "Image file…", self.cover_from_file)
        m.addAction(icon("insert-link", "applications-internet"), "From link…", self.cover_from_url)
        m.addAction(icon("edit-paste"), "Paste image", self.cover_from_clipboard)
        if self.cover_bytes:
            m.addAction(icon("transform-crop", "image-x-generic"), "Adjust cover…", self.edit_cover)
        if self.embedded_cover:
            m.addAction(icon("audio-x-generic"), "Use the file’s own cover",
                        lambda: self.set_cover(self.embedded_cover, "From file"))
        m.addSeparator()
        m.addAction(icon("edit-delete", "list-remove"), "No cover", lambda: self.set_cover(b"", ""))
        m.exec(self.cursor().pos())

    def edit_cover(self, data=None, source=None, trim=False, keep_label="Cancel"):
        dlg = CoverEditor(self, data or self.cover_bytes, trim, keep_label)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            src = source or self.cover_src
            self.set_cover(dlg.result, src if src.endswith("(adjusted)") else f"{src} (adjusted)")
            return True
        return False

    # ── YouTube ──
    def youtube_import(self):
        if self.loading or self.exporting:
            return
        if not yt_dlp_cmd():
            QMessageBox.information(self, APP, "Importing from YouTube needs yt-dlp.\n\n"
                                    "Install it with pip (pip install yt-dlp) or your package manager.")
            return
        if not self.confirm_discard("import something else"):
            return
        dlg = YouTubeDialog(self, self.settings)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return
        r = dlg.result
        self.dirty = False
        self._after_load = lambda: self.apply_youtube(r)
        self.open_file(Path(r["path"]))

    def apply_youtube(self, r):
        a = self.album
        self.e_artist.setText(r["artist"])
        self.e_album.setText(r["album"])
        self.e_year.setText(r["year"])
        es = r["entries"]
        if es and all(e["start"] is not None for e in es):
            titles = [e["title"] for e in es]
            artists = [e["artist"] for e in es]
            a.set_tracks([e["start"] for e in es[1:]], titles, artists)
            a.undo_stack.clear()
            where = "the video's chapters" if r.get("source") == "chapters" else "the tracklist"
            self.msg(f"Cut into {plural(len(a.tracks), 'track')} from {where}.", "ok", 12000)
        elif es:
            self.apply_untimed(es)
            a.undo_stack.clear()
        elif len(a.tracks) > 1:
            self.msg(f"No chapters. Found {plural(len(a.tracks), 'track')} at gaps; "
                     "import a tracklist to name them.", "ok", 12000)
        else:
            self.msg("No chapters and no gaps found. Import a tracklist with timestamps, "
                     "or play and press C where a track starts.", "warn",
                     action=("Import tracklist …", self.import_tracklist))
        if r["thumb"]:
            img = QImage.fromData(r["thumb"])
            if abs(img.width() - img.height()) > 4:
                if not self.edit_cover(r["thumb"], "YouTube thumbnail", trim=True, keep_label="Keep original"):
                    self.set_cover(r["thumb"], "YouTube thumbnail")
            else:
                self.set_cover(r["thumb"], "YouTube thumbnail")
        self.update_example()

    def cover_from_bytes(self, data, source="dropped image"):
        self.set_cover(data, source)

    def cover_from_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose cover image",
                                           str(self.album.path.parent) if self.album.path else "",
                                           "Images (*.jpg *.jpeg *.png *.webp *.bmp)")
        if f:
            data = Path(f).read_bytes()
            if Path(f).suffix.lower() in (".webp", ".bmp"):
                data = self.to_jpeg(data)
            self.set_cover(data, Path(f).name)

    def cover_from_clipboard(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            self.msg("No image in the clipboard.", "warn", 5000)
            return
        self.set_cover(self.to_jpeg(img), "pasted image")

    @staticmethod
    def to_jpeg(src):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImage
        img = src if isinstance(src, QImage) else QImage.fromData(src)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "JPG", 92)
        return bytes(ba)

    def cover_from_url(self):
        url, ok = QInputDialog.getText(self, "Cover from link", "Image link (https://…):")
        if ok and url.strip():
            self.fetch_cover(url.strip(), "From link")

    def fetch_cover(self, url, source):
        self.msg("Downloading cover…", "info", busy=True)
        lid = self.load_id
        self._fetch = Fetch(url)

        def got(data, err):
            if lid != self.load_id:
                return
            img = QPixmap()
            if err or not data or not img.loadFromData(data):
                keep = " Using the preview." if "preview" in self.cover_src else ""
                self.msg(f"Couldn't download the full-size cover.{keep}", "warn")
                return
            if data[:3] != b"\xff\xd8\xff" and image_ext(data) != ".png":
                data = self.to_jpeg(data)
            self.set_cover(data, source)
            self.msg(f"Cover: {source} ({img.width()}×{img.height()})", "ok", 5000)
        self._fetch.done.connect(got)
        keep(self._fetch)

    def cover_search(self):
        dlg = CoverDialog(self, self.e_artist.text() or "", self.e_album.text() or "")
        if dlg.exec() == QDialog.Accepted and dlg.choice:
            src, label, _, full, thumb = dlg.choice
            self.set_cover(thumb, f"{src} (preview)")
            self.fetch_cover(full, f"{src}: {label}")

    def auto_cover(self):
        artist, album = self.e_artist.text().strip(), self.e_album.text().strip()
        if not album:
            return
        lid = self.load_id
        self._auto = CoverSearch(artist, album)

        def got(res):
            if not res or lid != self.load_id or self.cover_bytes:
                return
            src, label, _, full, thumb = res[0]
            self.msg(f"Found a cover on {src}: {label}", "info", 20000,
                     action=("Use it", lambda: (self.set_cover(thumb, f"{src} (preview)"),
                                                self.fetch_cover(full, f"{src}: {label}"))))
        self._auto.done.connect(got)
        keep(self._auto)

    # ── detection / tracklist ──
    def detect_live(self):
        db = self.s_db.value()
        mn = self.s_min.value() / 10
        self.l_db.setText(f"{db} dB")
        self.l_min.setText(f"{mn:.1f} s")
        self.album.detect(db, mn)
        if hasattr(self, "wave"):
            self.wave.thresh = db
            self.wave.update()
        if self.album.env is None:
            self.l_cand.setText("")
            return
        c = len(self.album.candidates)
        n = self.sp_n.value()
        use = min(c, n - 1) if n else c
        self.l_cand.setText(f"{plural(c, 'gap')} found → {plural(use + 1, 'track')}")
        self.b_detect.setText(f" Cut at these {plural(use, 'gap')}" if use else " No gaps found")

    def apply_detection(self):
        a = self.album
        if a.env is None:
            return
        n = self.sp_n.value()
        c = len(a.candidates)
        new = (min(c, n - 1) if n else c) + 1
        if a.undo_stack and QMessageBox.question(
                self, APP, f"Replace the current {plural(len(a.tracks), 'track')} with {new}?\n\n"
                           "Names are kept in order.") != QMessageBox.Yes:
            return
        a.apply_detection(n)
        got = len(a.tracks)
        self.mark_dirty()
        if n and got < n:
            self.msg(f"Found only {got} of {n} tracks. Move “Silence level” right, lower “Shortest gap”, "
                     "or add the missing cuts by hand.", "warn")
        else:
            self.msg(f"Cut into {plural(got, 'track')}. Ctrl+Z undoes this.", "ok")

    def import_tracklist(self):
        if not self.album.path:
            self.msg("Open a file first.", "warn", 5000)
            return
        clip = QApplication.clipboard().text()
        text = clip if len(parse_tracklist(clip)[0]) >= 2 and len(clip) < 20000 else ""
        dlg = ImportDialog(self, self.album, text)
        if dlg.exec() != QDialog.Accepted or not dlg.entries:
            return
        a = self.album
        es = dlg.entries
        titles = [e["title"] for e in es]
        artists = [e["artist"] for e in es]
        self.mark_dirty()
        a.name_queue = None
        if not dlg.cuts:
            a.set_names(titles, artists)
            have = len(a.tracks)
            if len(es) < have:
                self.msg(f"Named {len(es)} of {have} tracks. The list has only {plural(len(es), 'entry')}.", "warn")
            elif len(es) > have:
                self.msg(f"Named all {have} tracks. The last {plural(len(es) - have, 'list entry')} "
                         "were not used.", "warn")
            else:
                self.msg(f"Named {plural(have, 'track')}.", "ok")
            return
        if dlg.info.get("times") is not None and all(e["start"] is not None for e in es):
            starts = [e["start"] for e in es]
            snapped = 0
            if dlg.snap.isChecked():
                mids = [(x + y) / 2 for x, y in a.candidates]
                for k, s in enumerate(starts):
                    if k and mids:
                        best = min(mids, key=lambda m: abs(m - s))
                        if abs(best - s) <= 3:
                            starts[k] = best
                            snapped += 1
            a.set_tracks(starts[1:], titles, artists)
            self.msg(f"Cut into {plural(len(a.tracks), 'track')} from the timestamps"
                     + (f" ({snapped} moved to a nearby gap)" if snapped else "") + ".", "ok")
        else:
            self.apply_untimed(es)

    def apply_untimed(self, es):
        """Names without times: cut at the biggest real gaps, or queue the names for manual cuts."""
        a = self.album
        titles = [e["title"] for e in es]
        artists = [e["artist"] for e in es]
        n = len(es)
        self.detect_live()
        if len(a.candidates) >= n - 1:
            a.apply_detection(n, titles, artists)
            a.name_queue = None
            self.msg(f"Cut at the {plural(n - 1, 'biggest gap')} and named all tracks. "
                     "Check the cuts with P.", "ok", 12000)
            return
        a.name_queue = (titles, artists)
        a.apply_detection(0, titles, artists)
        got = len(a.candidates)
        self.msg(f"Found {plural(got, 'gap')} for {n} tracks, so this is probably a continuous mix. "
                 "Play it and press C where each track starts. Names fill in in order.", "warn")

    # ── editing cuts ──
    def cut_here(self):
        i = self.album.add_cut(self.wave.playhead)
        if i:
            self.mark_dirty()
            self.table.selectRow(i)
        else:
            self.msg("Can't cut here: too close to another cut or to the start/end.", "warn", 4000)

    def delete_cuts(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if self.wave.hover_marker and self.wave.underMouse():
            rows = [self.wave.hover_marker]
        idx = rows
        idx = [i for i in idx if i > 0]
        if not idx:
            self.msg("Select a track after the first to remove the cut before it.", "warn", 5000)
            return
        self.album.remove_cuts(idx)
        self.mark_dirty()
        self.msg(f"Removed the cut before track {idx[0] + 1}." if len(idx) == 1
                 else f"Removed {len(idx)} cuts.", "ok", 6000)

    def nudge(self, dt):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows or rows[0] == 0:
            self.msg("Select a track after the first to move its cut.", "warn", 4000)
            return
        r = rows[0]
        a = self.album
        a.push_undo()
        a.move_cut(r, a.tracks[r][0] + dt)
        self.wave.ensure_visible(a.tracks[r][0])

    # ── table ──
    def on_tracks_changed(self):
        self.mark_dirty()
        a = self.album
        if a.name_queue:
            qt, qa = a.name_queue
            for i, tr in enumerate(a.tracks):
                tr[1] = qt[i] if i < len(qt) else None
                tr[2] = qa[i] if i < len(qa) else None
        t = self.table
        TT = TrackTable
        sel = sorted({i.row() for i in t.selectedIndexes()})
        t.blockSignals(True)
        t.setRowCount(len(a.tracks))
        t.setColumnHidden(TT.C_ARTIST, not a.has_artists())
        ro = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        italic = QFont(self.T["font"])
        italic.setItalic(True)
        warn = QBrush(self.T["neg"].darker(140))
        continuous = len(a.candidates) < max(1, (len(a.tracks) - 1) / 2)
        for i, (start, _, _) in enumerate(a.tracks):
            end = a.end(i)
            gap = a.gap_at(start) if i else None
            vals = ["", f"{i + 1}", a.artist(i) or "", a.title(i), fmt(start, 1), fmt(end, 1),
                    fmt(end - start, 0), f"{gap:.1f} s" if gap else ("" if i == 0 else "⚠ none")]
            for c, val in enumerate(vals):
                it = t.item(i, c)
                if it is None:
                    it = QTableWidgetItem()
                    t.setItem(i, c, it)
                it.setText(val)
                editable = c in (TT.C_ARTIST, TT.C_TITLE) or (c == TT.C_START and i > 0)
                it.setFlags(ro | (Qt.ItemIsEditable if editable else Qt.NoItemFlags))
                it.setToolTip("")
                it.setForeground(QBrush(self.T["text"]))
                it.setFont(self.T["font"])
                if c in (TT.C_NUM, TT.C_START, TT.C_END, TT.C_LEN, TT.C_GAP):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == TT.C_TITLE:
                    if a.tracks[i][1] is None:
                        it.setFont(italic)
                    it.setToolTip("F2 or Enter to rename")
                if c == TT.C_GAP and i and not gap and continuous:
                    it.setText("–")
                    it.setToolTip("Continuous recording: few gaps to cut at.")
                elif c == TT.C_GAP and i and not gap:
                    it.setForeground(warn)
                    it.setToolTip("No gap here. The cut may go through music. Press P to listen.")
                if c == TT.C_LEN and end - start < 15:
                    it.setForeground(warn)
                    it.setToolTip("Very short track. Del removes the cut before it.")
        t.blockSignals(False)
        if sel and sel[-1] < len(a.tracks):
            self.wave.sel_track = sel[0]
        self.wave.update()
        self.update_status()
        self.update_play_marker(force=True)
        self.update_example()
        self.update_enabled()
        self.detect_live()

    def table_edited(self, item):
        r, c = item.row(), item.column()
        a = self.album
        TT = TrackTable
        if c in (TT.C_TITLE, TT.C_ARTIST):
            a.name_queue = None
        if c == TT.C_TITLE:
            txt = item.text().strip()
            if txt != a.title(r):
                a.push_undo()
                a.tracks[r][1] = txt or None
                self.mark_dirty()
                a.changed.emit()
        elif c == TT.C_ARTIST:
            txt = item.text().strip() or None
            if txt != a.artist(r):
                a.push_undo()
                a.tracks[r][2] = txt
                self.mark_dirty()
                a.changed.emit()
        elif c == TT.C_START and r > 0:
            try:
                t = parse_time(item.text())
            except ValueError:
                self.msg("Type a time like 3:41 or 1:02:10.5.", "warn", 5000)
                a.changed.emit()
                return
            a.push_undo()
            a.move_cut(r, t)
            self.mark_dirty()
            self.wave.ensure_visible(a.tracks[r][0])

    def table_menu(self, pos):
        r = self.table.rowAt(pos.y())
        if r < 0 or self.album.path is None:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if r not in rows:
            self.table.selectRow(r)
            rows = [r]
        m = QMenu(self)
        m.addAction(icon("media-playback-start"), "Play", lambda: self.table_double(r, 0))
        if r > 0:
            m.addAction(icon("media-seek-forward", "media-playback-start"), "Listen to cut\tP",
                        self.preview_selected)
        m.addAction(icon("edit-rename", "document-edit"), "Rename\tF2",
                    lambda: (self.table.setCurrentCell(r, TrackTable.C_TITLE),
                             self.table.editItem(self.table.item(r, TrackTable.C_TITLE))))
        if any(x > 0 for x in rows):
            m.addAction(icon("list-remove", "edit-delete"), "Remove cut\tDel", self.delete_cuts)
        m.addSeparator()
        label = "Export this track…" if len(rows) == 1 else f"Export {len(rows)} selected tracks…"
        a = m.addAction(icon("document-save-as", "media-floppy"), label, self.export_selected)
        a.setEnabled(not self.exporting)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def table_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        self.wave.sel_track = rows[0] if rows else -1
        if rows:
            self.wave.ensure_visible(self.album.tracks[rows[0]][0])
        self.wave.update()

    def table_double(self, r, c):
        """Double-click a row = play that track from its start."""
        t = self.album.tracks[r][0]
        self.wave.ensure_visible(t)
        self.preview_end = None
        self.seek(t)
        self.player.play()

    # ── playback ──
    def playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play_pause(self):
        if not self.album.path:
            return
        self.preview_end = None
        if self.playing():
            self.player.pause()
        else:
            self.player.setPosition(int(self.wave.playhead * 1000))
            self.player.play()

    def stop(self):
        self.preview_end = None
        self.player.pause()
        self.seek(0)

    def seek(self, t):
        t = min(max(t, 0), self.album.total)
        self.player.setPosition(int(t * 1000))
        self._pos_ref = (t, time.monotonic())
        self.wave.playhead = t
        self.wave.update()

    def preview_at(self, t):
        if self.wave.span() > 60 or not (self.wave.v0 <= t <= self.wave.v1):
            self.wave.show_time(t, 20)
        self.seek(max(t - 4, 0))
        self.player.play()
        self.preview_end = t + 4

    def preview_selected(self):
        a = self.album
        if not a.path or len(a.tracks) < 2:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if rows and rows[0] > 0:
            t = a.tracks[rows[0]][0]
        else:
            t = min((x[0] for x in a.tracks[1:]), key=lambda s: abs(s - self.wave.playhead))
        self.preview_at(t)

    def next_cut(self):
        nxt = [x[0] for x in self.album.tracks if x[0] > self.wave.playhead + 0.05]
        if nxt:
            self.seek(nxt[0])

    def prev_cut(self):
        prv = [x[0] for x in self.album.tracks if x[0] < self.wave.playhead - 1.0]
        self.seek(prv[-1] if prv else 0)

    def tick(self):
        if not self.album.path or self.loading:
            return
        playing = self.playing()
        if playing:
            # the player reports its position in coarse steps: run the playhead on between them
            ref_pos, ref_t = self._pos_ref
            pos = ref_pos + (time.monotonic() - ref_t)
            pos = min(pos, ref_pos + 0.5, self.album.total)
            if self.preview_end is not None and pos >= self.preview_end:
                self.player.pause()
                self.preview_end = None
            self.wave.playhead = pos
            w = self.wave
            idle = time.monotonic() - w.user_view_t > 3
            if not w.drag and not w.press and idle and w.span() < self.album.total:
                span = w.span()
                if pos > w.v0 + span * 0.7:          # scroll along smoothly
                    w.set_view(pos - span * 0.7, pos + span * 0.3, user=False)
                elif pos < w.v0:                      # jumped back out of view
                    w.set_view(pos - span * 0.3, pos + span * 0.7, user=False)
            self.wave.update()
            self.analyzer.feed(self.album.spectrum(pos))
        elif self.analyzer.levels.any() or self.analyzer.peaks.any():
            self.analyzer.feed(np.zeros_like(self.analyzer.levels))
        if not self.seekbar.isSliderDown():
            self.seekbar.blockSignals(True)
            self.seekbar.setMaximum(int(self.album.total * 1000))
            self.seekbar.setValue(int(self.wave.playhead * 1000))
            self.seekbar.blockSignals(False)
        self.l_pos.setText(fmt(self.wave.playhead, 0))
        self.l_len.setText(fmt(self.album.total, 0))
        ic = "media-playback-pause" if playing else "media-playback-start"
        if getattr(self, "_play_ic", None) != ic:
            self._play_ic = ic
            self.a_play.setIcon(icon(ic))
        self.update_play_marker()

    def update_play_marker(self, force=False):
        if not self.album.tracks:
            return
        cur = self.album.track_at(self.wave.playhead)
        st = "pause" if self.playing() else "start"
        key = (cur, st, self.table.rowCount())
        if not force and getattr(self, "_pm", None) == key:
            return
        self._pm = key
        ic = icon(f"media-playback-{st}")
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setIcon(ic if r == cur else QIcon())

    # ── scrollbar ──
    def sync_scroll(self):
        tot = self.album.total
        sb = self.hscroll
        sb.blockSignals(True)
        span = self.wave.span()
        sb.setRange(0, max(int((tot - span) * 10), 0))
        sb.setPageStep(max(int(span * 10), 1))
        sb.setSingleStep(max(int(span), 1))
        sb.setValue(int(self.wave.v0 * 10))
        sb.blockSignals(False)

    def scroll_moved(self, v):
        span = self.wave.span()
        self.wave.set_view(v / 10, v / 10 + span)

    # ── export ──
    def choose_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "Export tracks to", self.e_out.text())
        if d:
            self.e_out.setText(d)
            self.settings.setValue("export_dir", d)
            self.update_example()
        return bool(d)

    EXPORT_KEYS = ("c_subdir", "c_num", "c_fartist", "c_falbum", "c_ftitle", "c_cover", "c_coverfile")

    def save_export_settings(self):
        st = self.settings
        st.setValue("format", self.cb_fmt.currentIndex())
        st.setValue("name_style", self.cb_sep.currentIndex())
        for k in self.EXPORT_KEYS:
            st.setValue(k, getattr(self, k).isChecked())
        if self.e_out.text():
            st.setValue("export_dir", self.e_out.text())

    def load_export_settings(self):
        st = self.settings
        self.cb_fmt.setCurrentIndex(int(st.value("format", 0)))
        self.cb_sep.setCurrentIndex(int(st.value("name_style", 0)))
        for k in self.EXPORT_KEYS:
            v = st.value(k)
            if v is not None:
                getattr(self, k).setChecked(v in (True, "true"))

    def target_dir(self):
        base = Path(self.e_out.text() or (self.album.path.parent if self.album.path else Path.home()))
        base = base.expanduser()
        if self.c_subdir.isChecked():
            sub = " - ".join(x for x in (self.e_artist.text().strip(), self.e_album.text().strip()) if x)
            if sub:
                base = base / safe(sub)
        return base

    def file_name(self, i):
        a = self.album
        parts = []
        if self.c_fartist.isChecked():
            parts.append(a.artist(i) or self.e_artist.text().strip())
        if self.c_falbum.isChecked():
            parts.append(self.e_album.text().strip())
        if self.c_ftitle.isChecked() or not (parts or self.c_num.isChecked()):
            parts.append(a.title(i))
        parts = [x for x in parts if x]
        style = self.cb_sep.currentIndex()
        name = [" - ", " - ", " ", "_"][style].join(parts)
        if self.c_num.isChecked():
            name = f"{i + 1:02d}" + ([" - ", ". ", " ", "_"][style] + name if name else "")
        return safe(name)

    def update_example(self, *_):
        if not hasattr(self, "l_example") or not hasattr(self, "e_artist"):
            return
        a = self.album
        ext = FORMATS[self.cb_fmt.currentIndex()][1] or (a.path.suffix.lower() if a.path else ".mp3")
        if a.path and a.tracks:
            ex = self.file_name(min(1, len(a.tracks) - 1)) + ext
            where = str(self.target_dir()).replace(str(Path.home()), "~", 1)
            self.l_example.setText(f"Example: <b>{ex}</b><br>in {where}")
        else:
            self.l_example.setText(f"Example: <b>01 - Title{ext}</b>")

    def export_button(self):
        if self.exporting:
            if QMessageBox.question(self, APP, "Stop the export?") == QMessageBox.Yes and self.exporter:
                self.exporter.stop = True
            return
        if self.album.path and self.choose_outdir():
            self.export()

    def export_selected(self):
        if self.exporting or not self.album.path:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            self.msg("Select one or more tracks first.", "warn", 4000)
            return
        if self.choose_outdir():
            self.export(rows)

    def export(self, rows=None):
        a = self.album
        if not a.path:
            self.msg("Open a file first.", "warn", 4000)
            return
        if self.exporting:
            return
        rows = list(range(len(a.tracks))) if rows is None else rows
        if all(a.tracks[i][1] is None for i in rows) and len(a.tracks) > 1:
            box = QMessageBox(QMessageBox.Question, APP,
                              "The tracks have no names yet. They will be saved as "
                              "“Track 01”, “Track 02” and so on.", parent=self)
            b_imp = box.addButton("Import tracklist…", QMessageBox.AcceptRole)
            b_go = box.addButton("Export anyway", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is b_imp:
                self.import_tracklist()
                return
            if box.clickedButton() is not b_go:
                return
        outdir = self.target_dir()
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP, f"Can't create the folder\n{outdir}\n\n{e.strerror}")
            return
        name, ext, acodec = FORMATS[self.cb_fmt.currentIndex()]
        if ext is None and a.path.suffix.lower() in (".ape", ".alac", ".tta", ".shn"):
            name, ext, acodec = FORMATS[1]  # can't write these containers: use FLAC
            self.msg(f"Can't copy {a.path.suffix} as is, exporting FLAC instead.", "info", 6000)
        ext = ext or a.path.suffix.lower()
        album_artist = self.e_artist.text().strip()
        jobs = [(a.tracks[i][0], a.end(i), a.title(i), a.artist(i), outdir / (self.file_name(i) + ext), i + 1)
                for i in rows]
        if len({j[4] for j in jobs}) < len(jobs):
            QMessageBox.warning(self, APP, "Some tracks would get the same file name.\n"
                                "Tick “Track number” on the Export page, or give the tracks names.")
            self.side.set_current(self.P_EXPORT)
            return
        existing = [j[4] for j in jobs if j[4].exists()]
        if existing and QMessageBox.question(
                self, APP, f"{outdir}\nalready contains {len(existing)} of these files.\n\nReplace them?") \
                != QMessageBox.Yes:
            return
        cover_path = None
        if self.cover_bytes:
            if self.c_coverfile.isChecked():
                (outdir / ("cover" + image_ext(self.cover_bytes))).write_bytes(self.cover_bytes)
            if self.c_cover.isChecked():
                tmp = tempfile.NamedTemporaryFile(prefix="cuttycat-", suffix=image_ext(self.cover_bytes),
                                                  delete=False)
                tmp.write(self.cover_bytes)
                tmp.close()
                cover_path = Path(tmp.name)
        self._cover_tmp = cover_path
        if cover_path and ext in (".opus", ".ogg"):
            import importlib.util
            if not importlib.util.find_spec("mutagen"):
                self.msg("Covers in Opus/Ogg files need the Python package mutagen. Exporting without cover.",
                         "warn", 8000)
        meta = {"artist": album_artist, "album_artist": album_artist,
                "album": self.e_album.text().strip(), "date": self.e_year.text().strip()}
        self.last_outdir = outdir
        self.log.clear()
        self.log.show()
        self.log.appendPlainText(f"→ {outdir}  ({name})")
        self.msg(f"Exporting {plural(len(jobs), 'track')} to {outdir}…", "info", busy=True)
        self.msgbar.set_progress(0, len(jobs))
        self.b_export.setText(" Cancel export")
        self.b_export.setIcon(icon("process-stop", "dialog-cancel"))
        self.fails = []
        self.exporting = True
        self.update_enabled()
        self.save_export_settings()
        self.exporter = Exporter(a.path, jobs, acodec, meta, cover_path, total=len(a.tracks))
        self.exporter.progress.connect(self.export_progress)
        self.exporter.finished.connect(self.export_done)
        keep(self.exporter)

    def export_progress(self, k, name, ok, err):
        self.msgbar.set_progress(k, len(self.exporter.jobs))
        self.log.appendPlainText(("✓ " if ok else "✗ ") + name + ("" if ok else f"   ({err})"))
        if not ok:
            self.fails.append(f"{name}: {err}")

    def export_done(self):
        self.exporting = False
        self.b_export.setIcon(icon("document-save-as", "media-floppy"))
        self.update_enabled()
        if self._cover_tmp:
            self._cover_tmp.unlink(missing_ok=True)
        if self.exporter.stop:
            self.msg("Export cancelled.", "warn", 6000)
            return
        n = len(self.exporter.jobs) - len(self.fails)
        if self.fails:
            self.msg(f"Saved {plural(n, 'track')}, {len(self.fails)} failed. Details on the Export page.", "error")
            self.side.set_current(self.P_EXPORT)
            return
        self.dirty = False
        self.update_title()
        self.msg(f"Saved {plural(n, 'track')} to {self.last_outdir}", "ok",
                 action=("Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_outdir)))))

    # ── status ──
    def update_status(self):
        a = self.album
        self.l_status.setText("  ·  ".join([plural(len(a.tracks), "track")] +
                                           ([self.file_info] if getattr(self, "file_info", "") else []))
                              if a.path else "no file")

    # ── drag & drop / closing ──
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and self.album.path:
                    self.set_cover(p.read_bytes() if p.suffix.lower() != ".webp"
                                   else self.to_jpeg(p.read_bytes()), p.name)
                else:
                    self.open_file(p)
                break

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.album.path:
            QTimer.singleShot(0, self.update_now_playing)

    def closeEvent(self, e):
        if self.exporter and self.exporter.isRunning():
            if QMessageBox.question(self, APP, "An export is still running. Stop it and quit?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.exporter.stop = True
            self.exporter.wait(5000)
        elif not self.confirm_discard("quit"):
            e.ignore()
            return
        self.player.stop()
        self.settings.setValue("geometry", self.saveGeometry())
        self.save_export_settings()
        super().closeEvent(e)


def main():
    import os
    if sys.platform == "win32":
        # yt-dlp (system or bundled) should write UTF-8 into our pipes, see UTF8.
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        # Look for ffmpeg in the ffmpeg\ folder next to Cuttycat.exe (or next to the exe itself) before PATH.
        here = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent
        os.environ["PATH"] = os.pathsep.join([str(here / "ffmpeg"), str(here / "ffmpeg" / "bin"), str(here),
                                              os.environ.get("PATH", "")])
    if len(sys.argv) > 1 and sys.argv[1] == "--run-yt-dlp":   # see yt_dlp_cmd()
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        import yt_dlp
        sys.exit(yt_dlp.main(sys.argv[2:]))
    # Use the desktop's own file dialogs (KDE, GNOME …) through the XDG portal when available.
    if sys.platform.startswith("linux") and not os.environ.get("QT_QPA_PLATFORMTHEME") \
            and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setDesktopFileName("cuttycat")
    app.setWindowIcon(QIcon(str(DATA / "icon.png")))
    if getattr(sys, "frozen", False):
        # PyInstaller points LD_LIBRARY_PATH at its bundled libs; don't hand that to ffmpeg & co.
        orig = os.environ.get("LD_LIBRARY_PATH_ORIG")
        if orig is not None:
            os.environ["LD_LIBRARY_PATH"] = orig
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
    mode = QSettings("cuttycat", "cuttycat").value("theme", "auto")
    T = builtin_theme(resolve_mode(mode))
    MsgBar.dark = T["dark"]
    apply_theme(app, T)
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        how = ("Install it (for example: winget install Gyan.FFmpeg) or put ffmpeg.exe and "
               "ffprobe.exe next to Cuttycat.exe. Then start Cuttycat again." if sys.platform == "win32"
               else "Install ffmpeg and start Cuttycat again.")
        QMessageBox.critical(None, APP, "Cuttycat needs ffmpeg and ffprobe, but they are not on your PATH.\n\n" + how)
        sys.exit(1)
    w = Main(T)
    w.e_artist.textChanged.connect(lambda _: w.update_now_playing())
    w.e_album.textChanged.connect(lambda _: w.update_now_playing())
    w.show()
    if len(sys.argv) > 1:
        w.open_file(Path(sys.argv[1]).resolve())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
