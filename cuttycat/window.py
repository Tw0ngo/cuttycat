"""The main window."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np
from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QBrush, QDesktopServices, QFont, QFontMetrics, QIcon, QImage,
                           QKeySequence, QPalette, QPixmap)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollBar, QSizePolicy, QSlider, QSpinBox, QSplitter,
                               QStackedWidget, QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import __version__
from .audio import Exporter, Loader
from .common import APP, AUDIO_EXT, DATA, FORMATS, image_ext, keep, KOFI_URL, yt_dlp_cmd
from .covers import CoverDialog, CoverEditor, CoverLabel, CoverSearch, Fetch, to_jpeg
from .importdialog import ImportDialog
from .model import Album
from .theme import apply_palette, builtin_theme, icon, resolve_mode
from .tracklist import fmt, guess_from_filename, parse_time, parse_tracklist, plural, safe
from .waveform import Waveform
from .widgets import Analyzer, JumpSlider, MsgBar, SideBar, TrackTable, VolumeWedge
from .youtube import YouTubeDialog


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
                data = to_jpeg(data)
            self.set_cover(data, Path(f).name)

    def cover_from_clipboard(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            self.msg("No image in the clipboard.", "warn", 5000)
            return
        self.set_cover(to_jpeg(img), "pasted image")

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
                data = to_jpeg(data)
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
