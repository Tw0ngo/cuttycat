"""Playing, seeking and the play marker."""
import time

import numpy as np
from PySide6.QtGui import QIcon
from PySide6.QtMultimedia import QMediaPlayer

from ..theme import icon
from ..tracklist import fmt


class Playback:
    def seekbar_dragged(self, v):
        self.wave.playhead = v / 1000
        self.wave.ensure_visible(v / 1000)
        self.l_pos.setText(fmt(v / 1000, 0))
        self.wave.update()

    def update_now_playing(self):
        self.np_cover.set_image(self.cover)
        txt = " - ".join(x for x in (self.e_artist.text(), self.e_album.text()) if x)
        self.np_text.setText(txt or (self.album.path.name if self.album.path else ""))
        self.update_example()

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
