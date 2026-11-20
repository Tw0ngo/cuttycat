"""Export page settings and exporting the tracks."""
from pathlib import Path
import tempfile

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..audio import Exporter
from ..common import APP, FORMATS, image_ext, keep
from ..theme import icon
from ..tracklist import plural, safe


class ExportActions:
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
