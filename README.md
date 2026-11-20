# Cuttycat

Splits one long recording (album rip, DJ mix, YouTube upload) into separate tracks.

Cuttycat finds the gaps, lets you check and move every cut, names the tracks from a pasted
tracklist or a video's chapters, and exports them with tags and cover.

![Cuttycat](docs/screenshot.png)

## Features

- **Cuts at gaps**: the silence threshold is drawn on the waveform as you adjust it
- **Waveform editor**: drag cuts, double-click to add one, `P` plays the seconds around a cut
- **Tracklist import**: paste from Discogs, Bandcamp, MusicBrainz, a YouTube description …
  Track numbers, `Artist - Title` order and timestamps (start times or lengths) are detected
- **YouTube import** (via [yt-dlp](https://github.com/yt-dlp/yt-dlp)): downloads the audio, cuts at
  the chapters or the tracklist in the description, uses the thumbnail as cover
- **Cover**: from the file, an image, a link, or found online (iTunes, Deezer, MusicBrainz);
  crop 16:9 thumbnails to a square
- **Export** all tracks or just the selected ones, as the original audio (no re-encoding), FLAC,
  MP3, Opus or Ogg, with tags and cover
- Light and dark theme, following the system by default

| Dark theme | Export settings |
|---|---|
| ![Dark theme](docs/screenshot-dark.png) | ![Export](docs/screenshot-export.png) |

## Download

| System | Download |
|---|---|
| Windows (installer, recommended) | [cuttycat_install.exe](https://github.com/Tw0ngo/cuttycat/releases/latest/download/cuttycat_install.exe) |
| Windows (portable, no install) | [cuttycat-portable.zip](https://github.com/Tw0ngo/cuttycat/releases/latest/download/cuttycat-portable.zip) |
| Linux (x86_64, single file) | [cuttycat](https://github.com/Tw0ngo/cuttycat/releases/latest/download/cuttycat) |
| Arch Linux | [cuttycat-git](https://aur.archlinux.org/packages/cuttycat-git) in the AUR |

These links always point to the newest version. Older versions are on the
[releases page](https://github.com/Tw0ngo/cuttycat/releases).

### Windows

1. Download `cuttycat_install.exe` (link above).
2. Double-click it. If Windows shows "Windows protected your PC", click **More info** →
   **Run anyway** (the installer isn't signed with a paid certificate).
3. Click through the installer. Cuttycat then has a Start menu entry and a desktop shortcut.

Cuttycat is installed to `C:\Program Files\Cuttycat` (Windows asks once for permission).
Everything it needs, ffmpeg included, comes with it.
To uninstall, double-click **Uninstall** in the Cuttycat folder, or remove it under Settings → Apps.

Don't want to install? `cuttycat-portable.zip` is the same program: unzip it and run `Cuttycat.exe`.

### Linux

Download `cuttycat`, make it executable and run it:

```sh
chmod +x cuttycat
./cuttycat
```

Requires ffmpeg:

```sh
sudo apt install ffmpeg      # Debian, Ubuntu, Mint
sudo dnf install ffmpeg      # Fedora
sudo zypper install ffmpeg   # openSUSE
```

### Arch Linux (AUR)

```sh
yay -S cuttycat-git      # or: paru -S cuttycat-git
```

Without an AUR helper:

```sh
git clone https://aur.archlinux.org/cuttycat-git.git
cd cuttycat-git
makepkg -si
```

## Install from source

Needs Python 3.10+ and ffmpeg.

```sh
git clone https://github.com/Tw0ngo/cuttycat
cd cuttycat
pipx install ".[all]"      # or: pip install --user ".[all]"
cuttycat
```

`yt-dlp` (YouTube import) and `mutagen` (covers in Opus/Ogg files) are optional.

To get a menu entry, copy `cuttycat.desktop` to `~/.local/share/applications/` and
`cuttycat/assets/icon.png` to `~/.local/share/icons/hicolor/256x256/apps/cuttycat.png`.

## Using it

1. **Open** a file (or drop it on the window), or **Import from YouTube**.
2. **Tracks**: double-click a track to play it, `P` plays the seconds around the selected cut.
   Drag red lines to move cuts, double-click the waveform to add one, `Del` removes the cut before
   the selected track. Name the tracks with **Import tracklist** or `F2`.
3. **Export**: pick format, file names and cover on the Export page, then click **Export** and
   choose a folder. Right-click a track to export only that one.

`Ctrl+Z` undoes any change. Help → *Keyboard and mouse* lists all shortcuts.

When a file has no cover, Cuttycat looks one up on iTunes, Deezer and MusicBrainz (it sends the
artist and album name). Turn this off under Edit → Look for covers online.

Only download and split recordings you have the rights to.

## Building

```sh
pip install ".[all]" pyinstaller
./packaging/build-standalone.sh     # creates dist/cuttycat
```

On Windows (PowerShell):

```powershell
pip install ".[all]" pyinstaller
.\packaging\build-windows.ps1       # creates dist\cuttycat_install.exe and dist\cuttycat-portable.zip
```

Put `ffmpeg.exe`, `ffprobe.exe` and ffmpeg's license file into `packaging\ffmpeg\` first so they are
included. The installer needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`).

How releases are made: [packaging/README.md](packaging/README.md).

## License

Made by Twongo. Cuttycat is free software under the
[GNU General Public License, version 3 or later](LICENSE).

The Windows version includes ffmpeg (GPL v3, from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/));
its license is in the `ffmpeg` folder, and its `README.txt` there names the exact version and
where to get its source code.
