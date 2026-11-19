"""Suporte ao app congelado (StudioClips.exe, feito com PyInstaller).

- Os arquivos do usuário (streamers.yaml, hashtags.txt, .env, data/) ficam ao lado do .exe;
  os recursos embutidos (assets/) ficam em sys._MEIPASS.
- O Clipador chama `sys.executable -m streamlink` e `-m yt_dlp`. No .exe, sys.executable é o próprio
  StudioClips.exe, então ele precisa entender `-m <módulo>` e rodar o módulo, como o python faz.
- O FFmpeg vem junto na pasta `ffmpeg/` e entra no começo do PATH (vale pro yt-dlp e o streamlink também).
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dirs() -> tuple[Path, Path]:
    """(pasta dos arquivos do usuário, pasta dos recursos embutidos)."""
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir, Path(getattr(sys, "_MEIPASS", exe_dir))
    return _SOURCE_ROOT, _SOURCE_ROOT


def run_module_if_requested(argv: list[str]) -> bool:
    """`StudioClips.exe -m modulo args...` roda o módulo como `python -m`. Retorna False se não for o caso.
    O código de saída do módulo vira SystemExit (igual ao python)."""
    if len(argv) < 3 or argv[1] != "-m":
        return False
    module = argv[2]
    sys.argv = [module, *argv[3:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return True


def add_bundled_ffmpeg_to_path(app_dir: Path) -> bool:
    ff_dir = Path(app_dir) / "ffmpeg"
    if not (ff_dir / "ffmpeg.exe").exists() and not (ff_dir / "ffmpeg").exists():
        return False
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and p != str(ff_dir)]
    os.environ["PATH"] = os.pathsep.join([str(ff_dir), *parts])
    return True
