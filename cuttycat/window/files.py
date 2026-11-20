"""Opening files, recent files, YouTube import."""
from pathlib import Path
import subprocess
import sys

import numpy as np
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QDialog, QFileDialog, QListWidgetItem, QMenu, QMessageBox

from ..audio import Loader
from ..common import APP, AUDIO_EXT, yt_dlp_cmd
from ..theme import icon
from ..tracklist import fmt, guess_from_filename, plural
from ..youtube import YouTubeDialog


class FileActions:
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
