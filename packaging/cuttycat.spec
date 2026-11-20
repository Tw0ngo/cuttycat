# PyInstaller spec for the standalone build (Linux: dist/cuttycat, Windows: dist\Cuttycat\ folder).
# Used by build-standalone.sh and build-windows.ps1. Bundles Python, Qt, numpy, yt-dlp and mutagen.
# ffmpeg is not part of the spec: build-windows.ps1 copies it in, Linux uses the system's.
#
# Most of the size is Qt. PySide6's hooks pull in a lot that Cuttycat never uses (QML/Quick, PDF,
# the virtual keyboard, software OpenGL, ~250 translation files), so that is filtered out below.
# Qt's own FFmpeg libraries (avcodec …) stay: QMediaPlayer plays audio through them.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
WIN = sys.platform == "win32"

# Python modules that are never imported (keeps their hooks from collecting Qt libraries too).
EXCLUDES = [
    "tkinter", "unittest", "pydoc_data", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtGraphs", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtSvgWidgets",
    "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtHttpServer",
]

# Collected files (destination paths) that are dropped. Matched case-insensitively.
DROP = re.compile("|".join([
    r"(^|[/\\])translations[/\\]",                 # Qt UI translations (the app is English only)
    r"Qt6?(Quick|Qml|Pdf|VirtualKeyboard|WebEngine|WebChannel|WebSockets|3D|Charts|DataVisualization"
    r"|Graphs|Designer|ShaderTools|LabsStyleKit|OpenGLWidgets|Sql|Test|Help|Positioning|Location"
    r"|Sensors|Bluetooth|Nfc|SerialPort|SpatialAudio|TextToSpeech|RemoteObjects|Scxml|StateMachine)",
    r"opengl32sw\.dll",                            # Mesa software OpenGL fallback (20 MB), unused
    r"[/\\]qml[/\\]",
    r"[/\\]plugins[/\\](qmltooling|platforminputcontexts|virtualkeyboard|sqldrivers|designer"
    r"|sceneparsers|renderers|geometryloaders|position|sensors|texttospeech|canbus)[/\\]",
    r"[/\\]plugins[/\\]imageformats[/\\].*(pdf|svg)",
    r"[/\\]resources[/\\]qtwebengine",
]), re.I)


def keep(toc):
    return [entry for entry in toc if not DROP.search(entry[0])]


a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "cuttycat" / "assets"), "cuttycat/assets")],
    hiddenimports=collect_submodules("yt_dlp")
    + ["mutagen", "mutagen.flac", "mutagen.oggopus", "mutagen.oggvorbis"],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,
)
a.binaries = keep(a.binaries)
a.datas = keep(a.datas)

pyz = PYZ(a.pure)

if WIN:
    # Windows: a folder (dist\Cuttycat\Cuttycat.exe + _internal\) that the installer and the portable
    # zip ship. Starts faster than a single file, which unpacks itself to a temp folder on every start.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Cuttycat",
        icon=str(ROOT / "packaging" / "cuttycat.ico"),
        upx=False,         # UPX-packed Qt DLLs trip antivirus scanners
        console=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="Cuttycat", upx=False)
else:
    # Linux: one self-contained file.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="cuttycat",
        strip=True,        # strip debug symbols from the .so files
        upx=False,
        console=False,
    )
