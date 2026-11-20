"""Reading and exporting audio with ffmpeg."""
import json
from pathlib import Path
import re
import subprocess

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .common import BLK, image_ext, NOWIN, SR, UTF8


class Loader(QThread):
    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            p = str(self.path)
            info = json.loads(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,bit_rate:format_tags:stream=codec_type,codec_name:stream_tags",
                 "-of", "json", p], capture_output=True, check=True, **UTF8, **NOWIN).stdout)
            total = float(info["format"]["duration"])
            tags = {k.lower(): v for k, v in info["format"].get("tags", {}).items()}
            streams = info.get("streams", [])
            for s in streams:
                for k, v in s.get("tags", {}).items():
                    tags.setdefault(k.lower(), v)
            raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", p, "-vn", "-ac", "1",
                                  "-ar", str(SR), "-f", "s16le", "-"],
                                 capture_output=True, check=True, **NOWIN).stdout
            samples = np.frombuffer(raw, dtype=np.int16)
            n = samples.size // BLK
            peaks = np.abs(samples[:n * BLK].reshape(n, BLK).astype(np.int32)).max(1) / 32768.0
            env_db = (20 * np.log10(peaks + 1e-6)).astype(np.float32)
            env = np.clip((env_db + 60) / 60, 0, 1).astype(np.float32)
            cover, cover_src = b"", "From file"
            if any(s.get("codec_type") == "video" for s in streams):
                cover = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", p, "-an", "-frames:v", "1",
                                        "-c:v", "copy", "-f", "image2pipe", "-"],
                                       capture_output=True, **NOWIN).stdout
            if not cover:
                for f in sorted(self.path.parent.iterdir()):
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png") and re.search(
                            r"cover|folder|front|" + re.escape(self.path.stem[:20]), f.stem, re.I):
                        cover, cover_src = f.read_bytes(), f.name
                        break
            codec = next((s.get("codec_name", "") for s in streams
                          if s.get("codec_type") == "audio"), "")
            br = int(info["format"].get("bit_rate") or 0) // 1000
            self.done.emit(dict(total=total, tags=tags, samples=samples, env=env,
                                env_db=env_db, cover=cover, cover_src=cover_src, codec=codec, bitrate=br))
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            self.failed.emit("Can't read this file. It may not be audio, or it may be damaged.",
                             err.strip())
        except Exception as e:
            self.failed.emit("Can't read this file.", str(e))


class Exporter(QThread):
    progress = Signal(int, str, bool, str)

    def __init__(self, src, jobs, acodec, meta, cover_path=None, total=None):
        super().__init__()
        self.src, self.jobs, self.acodec, self.meta = src, jobs, acodec, meta
        self.cover_path = cover_path
        self.total = total or len(jobs)
        self.stop = False

    def run(self):
        for k, (s, e, title, artist, out, num) in enumerate(self.jobs):
            if self.stop:
                break
            inputs = ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(self.src)]
            tags = ["-map_metadata", "0", "-metadata", f"title={title}",
                    "-metadata", f"track={num}/{self.total}"]
            meta = dict(self.meta)
            if artist:
                meta["artist"] = artist
            for key, val in meta.items():
                if val:
                    tags += ["-metadata", f"{key}={val}"]
            if out.suffix.lower() == ".mp3":
                tags += ["-id3v2_version", "3"]
            acodec = ["-c:a", "copy"] if self.acodec is None else self.acodec
            if self.acodec is None and out.suffix.lower() == ".flac":
                # A stream-copied FLAC keeps the source's header, so players would show the length of the
                # whole recording. Re-encoding FLAC is lossless (same samples, same bit depth) and fixes it.
                acodec = ["-c:a", "flac"]
            head = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            ok = False
            if self.cover_path and out.suffix.lower() not in (".opus", ".ogg"):
                ok = subprocess.run(head + inputs + ["-i", str(self.cover_path), "-map", "0:a",
                                                     "-map", "1:v", "-c:v", "copy",
                                                     "-disposition:v", "attached_pic"]
                                    + acodec + tags + [str(out)],
                                    stdin=subprocess.DEVNULL, capture_output=True, **NOWIN).returncode == 0
            err = ""
            if not ok:
                r = subprocess.run(head + inputs + ["-map", "0:a"] + acodec + tags + [str(out)],
                                   stdin=subprocess.DEVNULL, capture_output=True, **NOWIN)
                ok = r.returncode == 0
                err = r.stderr.decode(errors="replace").strip().splitlines()[-1:] if not ok else []
                err = err[0] if err else ""
            if ok and self.cover_path and out.suffix.lower() in (".opus", ".ogg"):
                embed_ogg_cover(out, self.cover_path)
            self.progress.emit(k + 1, out.name, ok, err)


def embed_ogg_cover(path, cover_path):
    """ffmpeg can't put pictures into Ogg/Opus – do it with mutagen if available."""
    try:
        import base64
        from mutagen import File
        from mutagen.flac import Picture
        data = Path(cover_path).read_bytes()
        img = QImage.fromData(data)   # not QPixmap: this runs in the export thread
        pic = Picture()
        pic.type, pic.data = 3, data
        pic.mime = "image/png" if image_ext(data) == ".png" else "image/jpeg"
        pic.width, pic.height, pic.depth = img.width(), img.height(), 24
        f = File(str(path))
        f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
        f.save()
    except Exception:
        pass
