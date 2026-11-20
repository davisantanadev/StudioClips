"""Salvar vídeo e capa direto nas pastas escolhidas, com nome bonito e a extensão certa.

Antes o botão de baixar abria o "Salvar como" do Windows com o filtro "Todos os arquivos": quem renomeava sem
digitar ".mp4" ficava com um arquivo sem extensão (o Windows oferecia abrir com outro programa e o vídeo "sumia").
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .texts import strip_emoji

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # proibido em nome de arquivo no Windows
MAX_NAME = 90


def export_name(display: str, title: str, clip_id: int) -> str:
    """'Alanzoka - QUE PORRA FOI ESSA' (sem emoji, sem caractere proibido, sem ponto no fim)."""
    def clean(s: str) -> str:
        s = INVALID.sub(" ", strip_emoji(s or ""))
        return re.sub(r"\s+", " ", s).strip(" .")
    who, t = clean(display) or "clipe", clean(title)
    t = t or f"clipe {clip_id}"
    name = f"{who} - {t}"
    return name[:MAX_NAME].rstrip(" .")


def default_dirs() -> tuple[Path, Path]:
    home = Path.home()
    return home / "Videos" / "StudioClips", home / "Pictures" / "StudioClips"


def resolve_dirs(video_dir: str, cover_dir: str) -> tuple[Path, Path]:
    dv, dc = default_dirs()
    return (Path(video_dir) if video_dir else dv), (Path(cover_dir) if cover_dir else dc)


def unique(path: Path) -> Path:
    """Não sobrescreve: 'nome.mp4' já existe → 'nome (2).mp4'."""
    if not path.exists():
        return path
    n = 2
    while (p := path.with_name(f"{path.stem} ({n}){path.suffix}")).exists():
        n += 1
    return p


def export_clip(clip, data_dir: Path, kind: str, video_dir: Path, cover_dir: Path) -> list[str]:
    """Copia o vídeo e/ou a capa pras pastas. `kind`: video | cover | both. Devolve onde ficou cada um."""
    base = export_name(clip.display, clip.title, clip.id)
    jobs = []
    if kind in ("video", "both"):
        jobs.append((Path(data_dir) / clip.video_path, Path(video_dir), ".mp4"))
    if kind in ("cover", "both"):
        jobs.append((Path(data_dir) / clip.cover_path, Path(cover_dir), ".jpg"))
    out = []
    for src, folder, ext in jobs:
        if not src.exists():
            raise FileNotFoundError(f"arquivo do clipe não encontrado: {src.name}")
        folder.mkdir(parents=True, exist_ok=True)
        dest = unique(folder / f"{base}{ext}")
        shutil.copy2(src, dest)
        out.append(str(dest))
    return out
