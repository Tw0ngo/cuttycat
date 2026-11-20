"""The open recording: audio, cuts and track names."""
import numpy as np
from PySide6.QtCore import QObject, Signal

from .common import EDGE, SR


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
