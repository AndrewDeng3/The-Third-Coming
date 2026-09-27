# PyInstaller spec: builds dist\TheThirdComing\TheThirdComing.exe (one-folder, no console).
#   .venv\Scripts\pyinstaller packaging\stickfigure.spec --noconfirm
# Nothing heavy is bundled: on first run the app's setup window installs Ollama + the AI models, and the
# voice models (Kokoro, Whisper) download to %LOCALAPPDATA%\StickFigure\models by themselves.
# packaging\installer.iss wraps the result into TheThirdComing-Setup.exe.
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH + "\\.."

datas, binaries, hiddenimports = [], [], []
ICON = ROOT + "/packaging/third_coming.ico"
datas += [(ICON, "packaging")]  # window/tray icon at runtime
# Packages with data files / DLLs PyInstaller can't discover on its own.
for pkg in ("faster_whisper", "ctranslate2", "kokoro_onnx", "espeakng_loader", "phonemizer", "language_tags",
            "segments", "csvw", "sqlite_vec", "onnxruntime", "tokenizers", "qasync"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("stickfigure")
hiddenimports += collect_submodules("winrt")
hiddenimports += ["comtypes.client", "comtypes.stream", "sounddevice"]

a = Analysis(
    [ROOT + "\\run_stickfigure.pyw"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "torch", "tensorflow", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TheThirdComing",
    icon=ICON,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TheThirdComing")
