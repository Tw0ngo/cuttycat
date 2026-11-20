"""Cuttycat – split one long recording (album, mix, playlist rip) into tracks at the gaps.

Start-up only; the window is in window.py. See Help > Keyboard & mouse in the app for all controls.
"""
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .common import APP, DATA
from .theme import apply_theme, builtin_theme, resolve_mode
from .widgets import MsgBar
from .window import Main


def main():
    import os
    if sys.platform == "win32":
        # yt-dlp (system or bundled) should write UTF-8 into our pipes, see UTF8.
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        # Look for ffmpeg in the ffmpeg\ folder next to Cuttycat.exe (or next to the exe itself) before PATH.
        here = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent
        os.environ["PATH"] = os.pathsep.join([str(here / "ffmpeg"), str(here / "ffmpeg" / "bin"), str(here),
                                              os.environ.get("PATH", "")])
    if len(sys.argv) > 1 and sys.argv[1] == "--run-yt-dlp":   # see yt_dlp_cmd()
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        import yt_dlp
        sys.exit(yt_dlp.main(sys.argv[2:]))
    # Use the desktop's own file dialogs (KDE, GNOME …) through the XDG portal when available.
    if sys.platform.startswith("linux") and not os.environ.get("QT_QPA_PLATFORMTHEME") \
            and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setDesktopFileName("cuttycat")
    app.setWindowIcon(QIcon(str(DATA / "icon.png")))
    if getattr(sys, "frozen", False):
        # PyInstaller points LD_LIBRARY_PATH at its bundled libs; don't hand that to ffmpeg & co.
        orig = os.environ.get("LD_LIBRARY_PATH_ORIG")
        if orig is not None:
            os.environ["LD_LIBRARY_PATH"] = orig
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
    mode = QSettings("cuttycat", "cuttycat").value("theme", "auto")
    T = builtin_theme(resolve_mode(mode))
    MsgBar.dark = T["dark"]
    apply_theme(app, T)
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        how = ("Install it (for example: winget install Gyan.FFmpeg) or put ffmpeg.exe and "
               "ffprobe.exe next to Cuttycat.exe. Then start Cuttycat again." if sys.platform == "win32"
               else "Install ffmpeg and start Cuttycat again.")
        QMessageBox.critical(None, APP, "Cuttycat needs ffmpeg and ffprobe, but they are not on your PATH.\n\n" + how)
        sys.exit(1)
    w = Main(T)
    w.e_artist.textChanged.connect(lambda _: w.update_now_playing())
    w.e_album.textChanged.connect(lambda _: w.update_now_playing())
    w.show()
    if len(sys.argv) > 1:
        w.open_file(Path(sys.argv[1]).resolve())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
