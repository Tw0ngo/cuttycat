"""Colours, fonts and icons."""
import configparser
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory

from .common import DATA


# Cuttycat's own retro palette – the same on every system.
PALETTE = dict(
    window=(143, 129, 110), button=(143, 129, 110), base=(217, 208, 201), alt=(230, 231, 232),
    text=(24, 22, 18), wtext=(24, 22, 18), sel=(62, 73, 118), seltext=(248, 248, 248),
    tip=(159, 143, 122), accent=(188, 173, 154), neg=(218, 68, 83), neutral=(246, 116, 0),
    inactive=(127, 140, 141),
)
# First installed family wins. Fonts dropped into assets/fonts/ are loaded automatically.
FONT_FAMILIES = ["DinaRemasterII", "DinaRemaster", "Dina", "Terminus", "Terminus (TTF)",
                 "Fixedsys Excelsior 3.01", "Px437 IBM VGA 8x16", "W95FA", "Monaco", "Consolas",
                 "DejaVu Sans Mono", "monospace"]
FONT_SIZE = 12


DARK_PALETTE = dict(
    window=(58, 52, 45), button=(74, 67, 58), base=(36, 34, 31), alt=(46, 43, 39),
    text=(226, 218, 206), wtext=(226, 218, 206), sel=(92, 110, 178), seltext=(250, 250, 250),
    tip=(74, 67, 58), accent=(112, 100, 84), neg=(236, 100, 108), neutral=(252, 156, 64),
    inactive=(150, 148, 140),
)


def system_prefers_dark(app=None):
    """KDE colour scheme first (text vs. background), then Qt/portal, then GTK settings."""
    home = Path.home() / ".config"

    def lum(v):
        r, g, b = (int(x) for x in v.split(",")[:3])
        return 0.299 * r + 0.587 * g + 0.114 * b

    try:
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.optionxform = str
        cfg.read(home / "kdeglobals")
        view = cfg["Colors:View"]
        return lum(view["ForegroundNormal"]) > lum(view["BackgroundNormal"])
    except Exception:
        pass
    try:
        app = app or QApplication.instance()
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    for f in (home / "gtk-4.0/settings.ini", home / "gtk-3.0/settings.ini"):
        try:
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.read(f)
            val = cfg["Settings"].get("gtk-application-prefer-dark-theme", "")
            if val.strip().lower() in ("1", "true"):
                return True
            if "dark" in cfg["Settings"].get("gtk-theme-name", "").lower():
                return True
        except Exception:
            pass
    return False


def resolve_mode(mode):
    return ("dark" if system_prefers_dark() else "light") if mode == "auto" else mode


def builtin_theme(mode="light"):
    pal = DARK_PALETTE if mode == "dark" else PALETTE
    t = {k: QColor(*v) for k, v in pal.items()}
    t["dark"] = mode == "dark"
    return t


def apply_palette(app, T):
    pal = QPalette(T["button"], T["window"])
    for role, c in [(QPalette.Base, T["base"]), (QPalette.AlternateBase, T["alt"]),
                    (QPalette.Text, T["text"]), (QPalette.WindowText, T["wtext"]),
                    (QPalette.ButtonText, T["wtext"]), (QPalette.Highlight, T["sel"]),
                    (QPalette.HighlightedText, T["seltext"]),
                    (QPalette.ToolTipBase, T["tip"]), (QPalette.ToolTipText, T["text"]),
                    (QPalette.PlaceholderText, T["inactive"]),
                    (QPalette.BrightText, T["seltext"]), (QPalette.Link, T["sel"].lighter(150 if T["dark"] else 100))]:
        pal.setColor(role, c)
    dis = QColor(T["inactive"])
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, dis)
    app.setPalette(pal)


def system_icon_theme():
    """The desktop's icon theme (KDE or GTK setting), if it can be found."""
    home = Path.home() / ".config"
    for f, sec, key in ((home / "kdeglobals", "Icons", "Theme"),
                        (home / "gtk-3.0/settings.ini", "Settings", "gtk-icon-theme-name"),
                        (home / "gtk-4.0/settings.ini", "Settings", "gtk-icon-theme-name")):
        try:
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.optionxform = str
            cfg.read(f)
            val = cfg[sec][key].strip()
            if val:
                return val
        except Exception:
            pass
    return ""


def apply_theme(app, T):
    from PySide6.QtGui import QFontDatabase
    app.setStyle(QStyleFactory.create("Windows"))
    apply_palette(app, T)
    fonts = DATA / "fonts"
    if fonts.is_dir():
        for f in sorted(fonts.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                QFontDatabase.addApplicationFont(str(f))
    have = set(QFontDatabase.families())
    family = next((f for f in FONT_FAMILIES if f in have), None)
    font = QFont(family, FONT_SIZE) if family else QFontDatabase.systemFont(QFontDatabase.FixedFont)
    T["font"] = font
    app.setFont(font)
    QIcon.setThemeSearchPaths([str(Path.home() / ".local/share/icons"), "/usr/share/icons"]
                              + QIcon.themeSearchPaths())
    theme = system_icon_theme()
    if theme:
        QIcon.setThemeName(theme)
    QIcon.setFallbackThemeName("hicolor")


# freedesktop icon name -> Qt's built-in icon, used when the icon theme lacks one
STD_ICONS = {
    "document-open": "SP_DialogOpenButton", "document-save-as": "SP_DialogSaveButton",
    "document-save": "SP_DialogSaveButton", "media-floppy": "SP_DialogSaveButton",
    "media-playback-start": "SP_MediaPlay", "media-playback-pause": "SP_MediaPause",
    "media-playback-stop": "SP_MediaStop", "media-skip-backward": "SP_MediaSkipBackward",
    "media-skip-forward": "SP_MediaSkipForward", "media-seek-forward": "SP_MediaSeekForward",
    "edit-undo": "SP_ArrowBack", "edit-redo": "SP_ArrowForward", "view-refresh": "SP_BrowserReload",
    "edit-find": "SP_FileDialogContentsView", "system-search": "SP_FileDialogContentsView",
    "folder": "SP_DirIcon", "folder-open": "SP_DirOpenIcon", "folder-music": "SP_DirIcon",
    "media-optical-audio": "SP_DriveCDIcon", "media-optical": "SP_DriveCDIcon",
    "audio-x-generic": "SP_MediaVolume", "dialog-information": "SP_MessageBoxInformation",
    "dialog-warning": "SP_MessageBoxWarning", "dialog-error": "SP_MessageBoxCritical",
    "dialog-ok-apply": "SP_DialogApplyButton", "process-stop": "SP_BrowserStop",
    "dialog-cancel": "SP_DialogCancelButton", "edit-delete": "SP_TrashIcon",
    "list-remove": "SP_TrashIcon", "application-exit": "SP_DialogCloseButton",
    "help-contents": "SP_DialogHelpButton", "help-about": "SP_MessageBoxQuestion",
    "download": "SP_ArrowDown", "go-down": "SP_ArrowDown", "applications-internet": "SP_DriveNetIcon",
    "image-x-generic": "SP_FileIcon", "insert-image": "SP_FileIcon", "edit-paste": "SP_FileDialogDetailedView",
    "document-open-recent": "SP_FileDialogBack", "list-add": "SP_FileDialogNewFolder",
}


def icon(*names):
    for n in names:
        if QIcon.hasThemeIcon(n):
            return QIcon.fromTheme(n)
    style = QApplication.style()
    for n in names:
        sp = getattr(QStyle.StandardPixmap, STD_ICONS.get(n, ""), None) if n in STD_ICONS else None
        if sp is not None and style:
            return style.standardIcon(sp)
    return QIcon()
