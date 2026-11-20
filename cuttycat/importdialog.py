"""The Import tracklist dialog."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QRadioButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .model import Album
from .theme import icon, PALETTE
from .tracklist import (fmt, ORDER_ARTIST_TITLE, ORDER_TITLE, parse_tracklist, plural, SEPARATORS,
                        TIMES_LENGTH, TIMES_START)


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
