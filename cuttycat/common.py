"""Constants and small helpers shared by all parts of the app."""
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request


APP = "Cuttycat"
DATA = Path(__file__).resolve().parent / "assets"
UA = "Cuttycat/1.0"
KOFI_URL = "https://ko-fi.com/twongo/?hidefeed=true&widget=true&embed=true"
EDGE = 5.0            # ignore gaps this close to start/end (seconds)
SR, BLK = 8000, 80    # analysis sample rate, block size -> 100 envelope values/s
AUDIO_EXT = ["*.mp3", "*.flac", "*.ogg", "*.opus", "*.m4a", "*.aac", "*.wav",
             "*.wma", "*.ape", "*.wv", "*.aiff", "*.aif", "*.mka", "*.webm",
             "*.mp4", "*.alac"]
FORMATS = [  # label, extension (None = same as source), audio codec args
    ("Original (no re-encoding)", None, None),
    ("FLAC", ".flac", ["-c:a", "flac"]),
    ("MP3 320 kbit/s", ".mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]),
    ("MP3 V0 (VBR)", ".mp3", ["-c:a", "libmp3lame", "-q:a", "0"]),
    ("Opus 160 kbit/s", ".opus", ["-c:a", "libopus", "-b:a", "160k"]),
    ("Ogg Vorbis q6", ".ogg", ["-c:a", "libvorbis", "-q:a", "6"]),
]


_THREADS = set()


def keep(thread):
    """Hold a reference until the thread finishes (a QThread destroyed while running aborts the app)."""
    _THREADS.add(thread)
    thread.finished.connect(lambda: _THREADS.discard(thread))
    thread.start()
    return thread


# Windows: no console window popping up for every ffmpeg call.
NOWIN = dict(creationflags=subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else {}
# Text output of ffprobe/yt-dlp is UTF-8 everywhere (Windows would otherwise decode it as cp1252).
UTF8 = dict(text=True, encoding="utf-8", errors="replace")


def yt_dlp_cmd():
    """System yt-dlp first (usually newest), then the copy bundled with the app."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    import importlib.util
    if importlib.util.find_spec("yt_dlp"):
        if getattr(sys, "frozen", False):          # standalone build: the app runs yt-dlp itself
            return [sys.executable, "--run-yt-dlp"]
        return [sys.executable, "-m", "yt_dlp"]
    return None


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def image_ext(data):
    return ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
