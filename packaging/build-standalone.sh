#!/bin/sh
# Builds dist/cuttycat: a single executable with Python, Qt, numpy, yt-dlp and mutagen inside.
# Needs: pip install ".[all]" pyinstaller   (run from the repository root)
# ffmpeg is not bundled – it has to be installed on the system.
# What goes into the file (and what is left out to keep it small) is set in packaging/cuttycat.spec.
set -e
cd "$(dirname "$0")/.."
pyinstaller --noconfirm --clean --workpath build/linux --distpath dist packaging/cuttycat.spec
echo "Built: dist/cuttycat ($(du -h dist/cuttycat | cut -f1))"
