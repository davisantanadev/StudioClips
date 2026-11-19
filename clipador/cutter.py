"""Recorta [pico - pre, pico + post] do buffer de segmentos, com teto de duração."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .recorder import NO_WINDOW, Segment, ffmpeg_bin


def clip_window(segments: list[Segment], peak_ts: float, pre_s: float, post_s: float,
                max_s: float) -> tuple[float, float]:
    """Janela (início, fim) em horário de relógio, limitada ao que existe no buffer e a `max_s`."""
    if not segments:
        raise ValueError("buffer vazio")
    total = min(pre_s + post_s, max_s)
    post = min(post_s, total)
    pre = total - post
    start, end = peak_ts - pre, peak_ts + post
    buf_start, buf_end = segments[0].start_ts, segments[-1].end_ts
    if end > buf_end:  # pico perto do fim: puxa a janela pra trás
        start -= end - buf_end
        end = buf_end
    if start < buf_start:  # pico perto do começo (live acabou de abrir)
        end = min(buf_end, end + (buf_start - start))
        start = buf_start
    return start, min(end, start + max_s)


def cut_clip(segments: list[Segment], peak_ts: float, pre_s: float, post_s: float,
             max_s: float, out: Path) -> tuple[Path, float, float]:
    """Gera `out` (mp4). Retorna (caminho, duração, deslocamento do pico dentro do clipe)."""
    start, end = clip_window(segments, peak_ts, pre_s, post_s, max_s)
    used = [s for s in segments if s.end_ts > start and s.start_ts < end]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{s.path.resolve().as_posix()}'\n" for s in used), encoding="utf-8")
    offset = start - used[0].start_ts
    dur = end - start
    # 1) junta os segmentos sem recodificar (começa num keyframe)
    joined = out.with_name(out.stem + "_join.ts")
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(joined)],
                   check=True, creationflags=NO_WINDOW, timeout=300)
    # 2) corte exato com recodificação rápida (sem frames quebrados no início)
    from .render import video_encoder
    enc = video_encoder(fast=True)
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{max(offset, 0):.3f}", "-i", str(joined), "-t", f"{dur:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0?", *enc, "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(out)], check=True, creationflags=NO_WINDOW, timeout=600)
    joined.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    return out, dur, max(0.0, peak_ts - start)
