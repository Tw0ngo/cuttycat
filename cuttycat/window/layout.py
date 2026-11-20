"""Builds the window: pages, groups, player bar."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton,
                               QScrollBar, QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget,
                               QToolBar, QToolButton, QVBoxLayout, QWidget)

from ..common import FORMATS
from ..covers import CoverLabel
from ..theme import icon
from ..waveform import Waveform
from ..widgets import Analyzer, JumpSlider, MsgBar, SideBar, TrackTable, VolumeWedge


class Layout:
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
