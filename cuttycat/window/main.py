"""The main window."""
from pathlib import Path
import time

from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPalette, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from .. import __version__
from ..common import APP, DATA, KOFI_URL
from ..covers import to_jpeg
from ..model import Album
from ..theme import apply_palette, builtin_theme, icon, resolve_mode
from ..tracklist import plural
from ..widgets import MsgBar
from .cover import CoverActions
from .cuts import CutActions
from .export import ExportActions
from .files import FileActions
from .layout import Layout
from .playback import Playback


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


class Main(Layout, FileActions, CoverActions, CutActions, Playback, ExportActions, QMainWindow):
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

    def msg(self, text, level="info", ms=None, action=None, busy=False):
        self._last_level = level
        if ms is None and level in ("info", "ok") and not busy and not action:
            ms = 9000
        self.msgbar.show_msg(text, level, ms, action, busy)

    def flash(self, text, ms=None):
        self.msg(text, "info", ms)

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

    def update_status(self):
        a = self.album
        self.l_status.setText("  ·  ".join([plural(len(a.tracks), "track")] +
                                           ([self.file_info] if getattr(self, "file_info", "") else []))
                              if a.path else "no file")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and self.album.path:
                    self.set_cover(p.read_bytes() if p.suffix.lower() != ".webp"
                                   else to_jpeg(p.read_bytes()), p.name)
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
