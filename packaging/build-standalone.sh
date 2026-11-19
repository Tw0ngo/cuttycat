#!/bin/sh
# Builds dist/cuttycat: a single executable with Python, Qt, numpy, yt-dlp and mutagen inside.
# Needs: pip install ".[all]" pyinstaller   (run from the repository root)
# ffmpeg is not bundled – it has to be installed on the system.
set -e
cd "$(dirname "$0")/.."
pyinstaller --noconfirm --clean --onefile --windowed \
    --name cuttycat \
    --add-data "$PWD/cuttycat/assets:cuttycat/assets" \
    --collect-all yt_dlp \
    --hidden-import mutagen --hidden-import mutagen.flac --hidden-import mutagen.oggopus \
    --hidden-import mutagen.oggvorbis \
    --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets \
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml --exclude-module PySide6.Qt3DCore \
    --exclude-module PySide6.QtPdf --exclude-module tkinter \
    --specpath build \
    "$PWD/packaging/launcher.py"
echo "Built: dist/cuttycat ($(du -h dist/cuttycat | cut -f1))"
