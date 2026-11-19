"""Clipe que ficou no disco mas sumiu da lista (ex.: a pasta não pôde ser apagada porque o vídeo estava aberto)
volta pra Lixeira, com card, título e descrição de quando foi feito. Nada volta pra fila sem você decidir."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import db

log = logging.getLogger(__name__)
GONE_MARK = ".apagado"  # pasta apagada de vez pelo usuário, mas que o Windows não deixou remover


def _read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _created_at(d: Path) -> float:
    try:
        return time.mktime(time.strptime(d.name[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return (d / "final.mp4").stat().st_mtime


def _transcript(d: Path) -> tuple[str, str]:
    j = _read_json(d / "transcript.json")
    t = j.get("pt") or j.get("original") or {}
    segs = t.get("segments") or []
    return " ".join(str(s.get("text", "")).strip() for s in segs if isinstance(s, dict)).strip(), str(t.get("lang", ""))


def _duration(video: Path) -> float:
    try:
        from .render import probe_duration
        return round(probe_duration(video), 1)
    except Exception:
        return 0.0


def ensure_cover(d: Path) -> None:
    """Capa que faltou: um quadro do próprio vídeo final (já vertical, com o card)."""
    cover = d / "capa.jpg"
    if cover.exists() or not (d / "final.mp4").exists():
        return
    import subprocess
    from .recorder import NO_WINDOW, ffmpeg_bin
    try:
        subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", "2", "-i",
                        str(d / "final.mp4"), "-frames:v", "1", "-q:v", "3", str(cover)],
                       capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except Exception:
        pass


def recover_orphans(data_dir: Path, displays: dict[str, str]) -> int:
    """Põe na Lixeira os clipes prontos (com final.mp4) que não estão no banco. Devolve quantos voltaram."""
    root = Path(data_dir) / "clips"
    if not root.exists():
        return 0
    known = db.known_workdirs()
    n = 0
    for d in sorted(root.iterdir()):
        rel = f"clips/{d.name}"
        if not d.is_dir() or rel in known or not (d / "final.mp4").exists() or (d / GONE_MARK).exists():
            continue
        sel, meta = _read_json(d / "selecao.json"), _read_json(d / "meta.json")
        streamer = str(meta.get("streamer") or d.name[16:] or "?")
        # achado sozinho ("auto-kick-luanz7" / pasta "autokickluanz7") que agora está na lista: usa o da lista
        base = re.sub(r"^auto-?(kick|twitch|youtube)-?", "", streamer)
        if streamer not in displays and base in displays:
            streamer = base
        ensure_cover(d)
        text, lang = _transcript(d)
        card = sel.get("card") if isinstance(sel.get("card"), list) else []
        tags = sel.get("hashtags") if isinstance(sel.get("hashtags"), list) else []
        db.add_clip(db.Clip(
            streamer=streamer, display=displays.get(streamer, streamer), workdir=rel,
            video_path=f"{rel}/final.mp4", cover_path=f"{rel}/capa.jpg",
            card=[str(x) for x in card], title=str(sel.get("title") or f"{displays.get(streamer, streamer)} no GTA 6"),
            description=str(sel.get("description") or ""), hashtags=[str(x) for x in tags], transcript=text,
            lang=lang, platform=str(meta.get("platform") or ""), created_at=_created_at(d), deleted_at=time.time(),
            duration=_duration(d / "final.mp4")))
        n += 1
    if n:
        log.info("♻ %d clipe(s) que estavam no disco mas fora da lista voltaram pra Lixeira", n)
    return n
