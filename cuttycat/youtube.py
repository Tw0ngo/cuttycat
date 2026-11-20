"""Import from YouTube with yt-dlp."""
import json
from pathlib import Path
import re
import subprocess
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QToolButton, QVBoxLayout)

from .common import http_get, keep, NOWIN, UTF8, yt_dlp_cmd
from .importdialog import ImportDialog
from .theme import icon
from .tracklist import fmt, guess_from_filename, plural, yt_entries


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
