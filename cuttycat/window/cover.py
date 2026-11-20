"""Setting and finding the cover."""
from pathlib import Path

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMenu

from ..common import image_ext, keep
from ..covers import CoverDialog, CoverEditor, CoverSearch, Fetch, to_jpeg
from ..theme import icon


class CoverActions:
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
