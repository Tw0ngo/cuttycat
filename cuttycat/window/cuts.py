"""Gap detection, tracklist import, editing cuts and the track table."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QTableWidgetItem

from ..common import APP
from ..importdialog import ImportDialog
from ..theme import icon
from ..tracklist import fmt, parse_time, parse_tracklist, plural
from ..widgets import TrackTable


class CutActions:
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
