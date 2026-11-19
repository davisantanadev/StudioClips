# -*- mode: python ; coding: utf-8 -*-
# Monta dist/StudioClips/StudioClips.exe (pasta, sem console).  Rode pelo packaging/build.ps1.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

hidden = (
    collect_submodules("clipador")
    + collect_submodules("streamlink")       # plugins são carregados por nome
    + collect_submodules("streamlink_cli")
    + collect_submodules("yt_dlp")
    + ["webview", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]
)

datas = [
    (os.path.join(ROOT, "assets", "fonts"), os.path.join("assets", "fonts")),
    (os.path.join(ROOT, "assets", "models"), os.path.join("assets", "models")),
    (os.path.join(ROOT, "assets", "studioclips.ico"), "assets"),  # ícone da janela e da barra de tarefas
    (os.path.join(ROOT, "clipador", "web", "static"), os.path.join("clipador", "web", "static")),
    (os.path.join(ROOT, "clipador", "defaults"), os.path.join("clipador", "defaults")),
]
datas += collect_data_files("streamlink") + collect_data_files("webview") + collect_data_files("chat_downloader")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    hiddenimports=hidden,
    datas=datas,
    # Whisper local fica de fora do .exe (+300 MB); com o Groq ele não faz falta
    excludes=["faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "huggingface_hub",
              "tkinter", "matplotlib", "pytest", "IPython"],
    module_collection_mode={"streamlink.plugins": "pyz+py"},
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StudioClips",
    console=False,
    icon=os.path.join(ROOT, "assets", "studioclips.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="StudioClips", upx=False)
