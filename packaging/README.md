# Packaging

All versions are built from the same code in `cuttycat/`. There is no per-platform copy of the
program, so a fix or feature goes into `cuttycat/` once and every version gets it.

| Version | Built by | Notes |
|---|---|---|
| Linux standalone `cuttycat` | `build-standalone.sh` | uses `cuttycat.spec`, which leaves out the parts of Qt Cuttycat doesn't use |
| Windows `cuttycat_install.exe` + `cuttycat-portable.zip` | `build-windows.ps1` | uses `cuttycat.spec`, adds ffmpeg from `ffmpeg/`, installer from `windows/cuttycat.iss` |
| Arch AUR `cuttycat-git` | `aur/PKGBUILD` | builds from the git repository, uses Arch's PySide6 / numpy / ffmpeg |
| pip / pipx | `pyproject.toml` | |

## Making a release

1. Change the version in `cuttycat/__init__.py` (the only place it is written).
2. Commit, push, then tag: `git tag v0.2.0 && git push --tags`.
3. GitHub Actions (`.github/workflows/release.yml`) runs the tests, builds the Linux file and the
   Windows installer and portable zip and attaches them to a new release.
4. AUR users get the new version with their next update; the PKGBUILD only needs changing when
   dependencies change. After editing it: `makepkg --printsrcinfo > .SRCINFO`, commit both and push
   to `ssh://aur@aur.archlinux.org/cuttycat-git.git`.

## When to touch these files

- **New Python dependency:** add it to `pyproject.toml`, to `depends`/`optdepends` in `aur/PKGBUILD`
  (Arch package name) and, if PyInstaller misses it, to `hiddenimports` in `cuttycat.spec`.
- **New asset (image, font):** put it in `cuttycat/assets/`. All builds pick it up from there.
- **Platform-specific behaviour:** keep it in the code behind `sys.platform` checks
  (see `NOWIN` and `UTF8` in `common.py`, `show_in_folder` in `window/files.py`, `main` in `app.py`),
  not in the build scripts.
- **Using another Qt module:** remove it from `EXCLUDES`/`DROP` in `cuttycat.spec`, otherwise the
  standalone builds leave it out.
