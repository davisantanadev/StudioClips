"""Do trecho cru ao clipe pronto: transcreve → traduz → textos → legenda → render → capa → banco."""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path

from . import db
import subprocess

from .ai import Transcript, any_key, transcribe, whisper_prompt
from .recorder import NO_WINDOW, ffmpeg_bin
from .reframe import Framing, analyze
from .selector import select_moment, slice_transcript
from .config import Settings, Streamer
from .render import make_cover, probe_duration, render_vertical, sample_frames, subtitle_y
from .webcam import detect_webcam
from .detector import describe
from .subtitles import build_ass
from .texts import PostTexts, finish_texts, fit_card, make_texts, refine_card, translate_transcript

log = logging.getLogger(__name__)


def new_workdir(settings: Settings, streamer: Streamer) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = settings.data_dir / "clips" / f"{stamp}_{re.sub(r'[^a-z0-9_]', '', streamer.name.lower())}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def trim(src: Path, start: float, dur: float, out: Path) -> Path:
    """Corte exato (recodifica rápido) de [start, start+dur]."""
    from .render import video_encoder
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
                    "-i", str(src), "-t", f"{dur:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
                    *video_encoder(fast=True), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    str(out)], check=True, creationflags=NO_WINDOW)
    return out


def _framing(workdir: Path, meta: dict) -> Framing:
    """Enquadramento salvo no meta.json (calcula e salva na primeira vez)."""
    if meta.get("framing"):
        try:
            return Framing.from_json(meta["framing"])
        except (KeyError, TypeError, ValueError):
            pass
    webcam = tuple(meta["webcam"]) if meta.get("webcam") else None
    fr = analyze(workdir / "source.mp4", webcam)
    meta["framing"] = fr.to_json()
    (workdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return fr


def _render(workdir: Path, meta: dict, card: list[str], settings: Settings) -> tuple[Path, Path]:
    webcam = tuple(meta["webcam"]) if meta.get("webcam") else None
    zoom = meta.get("zoom") or settings.zoom  # mesmo zoom da legenda já posicionada
    framing = _framing(workdir, meta)
    ass = workdir / "subs.ass"
    final = render_vertical(workdir / "source.mp4", workdir / "final.mp4", webcam, card,
                            ass if ass.exists() else None, settings.handle, settings.accent,
                            card_s=settings.card_seconds or None, framing=framing, zoom=zoom)
    cover = make_cover(workdir / "source.mp4", meta.get("peak_offset", 1.0), workdir / "capa.jpg",
                       webcam, card, settings.handle, settings.accent, framing=framing, zoom=zoom)
    return final, cover


def find_webcam(streamer: Streamer, source: Path, settings: Settings) -> tuple[tuple | None, bool]:
    """Webcam marcada à mão vale primeiro; senão, acha sozinho no clipe. Devolve (caixa, foi_automática)."""
    if streamer.webcam:
        return tuple(streamer.webcam), False
    if not settings.auto_webcam:
        return None, False
    cam = detect_webcam(source, probe_duration(source))
    if cam:
        log.info("🎥 webcam de %s achada sozinha em %s", streamer.display, cam)
    return cam, cam is not None


def _finish(streamer: Streamer, workdir: Path, tr: Transcript, peak_offset: float, settings: Settings, *,
            platform: str, live_title: str, chat_snippets: list[str] | None,
            preset: PostTexts | None = None, t0: float | None = None) -> db.Clip:
    """Com o source.mp4 e a transcrição do clipe prontos: tradução → legenda → textos → render → banco."""
    t0 = t0 or time.time()
    is_pt = tr.lang.startswith("pt") if tr.lang else streamer.is_pt
    tr_pt = tr if is_pt else translate_transcript(tr, settings.groq_text_model)
    (workdir / "transcript.json").write_text(json.dumps(
        {"original": tr.to_json(), "pt": tr_pt.to_json()}, ensure_ascii=False, indent=1), encoding="utf-8")

    webcam, cam_auto = find_webcam(streamer, workdir / "source.mp4", settings)
    sub_y = subtitle_y(webcam, settings.zoom)
    mode = "karaoke" if is_pt else "phrase"
    (workdir / "subs.ass").write_text(build_ass(tr_pt, mode, settings.accent, y=sub_y), encoding="utf-8")

    if preset and preset.card and preset.title:
        texts = finish_texts(preset, streamer.display, platform or "live", settings.base_hashtags)
    else:
        texts = make_texts(streamer.display, platform or "live", tr_pt.text, live_title,
                           settings.base_hashtags, settings.groq_text_model, chat_snippets)

    if any_key("vision"):  # a IA "assiste" o clipe e reescreve o card com o que aparece de verdade
        # 3 quadros pequenos (começo, meio, fim): máx. do Groq e bem menos cota gasta por clipe.
        # É um extra: se falhar, o clipe sai com o card do texto em vez de ser perdido.
        try:
            better = refine_card(sample_frames(workdir / "source.mp4", 3, width=384), streamer.display,
                                 tr_pt.text, texts.card, texts.title, chat_snippets, settings.groq_vision_model)
            if better:
                texts.card = better
        except Exception:
            log.exception("card com visão falhou — mantendo o card do texto")

    meta = {"webcam": list(webcam) if webcam else None, "webcam_auto": cam_auto, "zoom": settings.zoom,
            "peak_offset": peak_offset, "streamer": streamer.name, "platform": platform,
            "live_title": live_title, "mode": mode}
    (workdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    final, cover = _render(workdir, meta, texts.card, settings)
    rel = lambda p: p.relative_to(settings.data_dir).as_posix()
    clip = db.Clip(streamer=streamer.name, display=streamer.display, platform=platform,
                   workdir=rel(workdir), video_path=rel(final), cover_path=rel(cover), card=texts.card,
                   title=texts.title, description=texts.description, hashtags=texts.hashtags,
                   transcript=tr_pt.text, lang=tr.lang, duration=round(probe_duration(final), 1))
    db.add_clip(clip)
    log.info("✅ clipe %s pronto em %.0fs: %s", streamer.display, time.time() - t0, texts.title)
    return clip


def process_clip(streamer: Streamer, src: Path, peak_offset: float, settings: Settings, *,
                 platform: str = "", live_title: str = "", chat_snippets: list[str] | None = None,
                 workdir: Path | None = None) -> db.Clip:
    """Trecho já cortado: transcreve e finaliza (sem escolha de momento pela IA)."""
    workdir = workdir or new_workdir(settings, streamer)
    source = workdir / "source.mp4"
    if Path(src).resolve() != source.resolve():
        shutil.move(str(src), source)
    t0 = time.time()
    tr = transcribe(source, streamer.lang, settings.groq_whisper_model, settings.whisper_local_model,
                    whisper_prompt(streamer.display, streamer.lang))
    return _finish(streamer, workdir, tr, peak_offset, settings, platform=platform, live_title=live_title,
                   chat_snippets=chat_snippets, t0=t0)


def process_candidate(streamer: Streamer, analysis: Path, peak_offset: float, settings: Settings, *,
                      platform: str = "", live_title: str = "", chat: list[tuple[float, str]] | None = None,
                      workdir: Path | None = None, manual: bool = False,
                      note=lambda m: log.info(m), signals: dict | None = None) -> db.Clip | None:
    """Janela longa (~2 min) → IA escolhe o melhor trecho (~1 min, até 90 s) → clipe. None se a IA achar fraco.
    `signals`: por que o detector disparou (risada/susto/grito), pra IA saber que tipo de momento é."""
    workdir = workdir or new_workdir(settings, streamer)
    t0 = time.time()
    chat = chat or []
    total = probe_duration(analysis)
    tr_full = transcribe(analysis, streamer.lang, settings.groq_whisper_model, settings.whisper_local_model,
                         whisper_prompt(streamer.display, streamer.lang))
    sel = select_moment(tr_full, chat, peak_offset, total, display=streamer.display, live_title=live_title,
                        min_s=settings.min_clip_s, max_s=settings.max_clip_s, model=settings.groq_text_model,
                        signals=describe(signals), ideal_s=settings.ideal_clip_s)
    (workdir / "selecao.json").write_text(json.dumps(vars(sel), ensure_ascii=False, indent=1), encoding="utf-8")
    if sel.by_ai and sel.score is not None and sel.score < settings.min_score and not manual:
        note(f"🗑 {streamer.display}: momento fraco (nota {sel.score:.0f}) — {sel.reason}")
        shutil.rmtree(workdir, ignore_errors=True)
        return None
    trim(analysis, sel.start, sel.dur, workdir / "source.mp4")
    analysis.unlink(missing_ok=True)
    (workdir / "audio.mp3").unlink(missing_ok=True)
    tr = slice_transcript(tr_full, sel.start, sel.end)
    snippets = [x for (t, x) in chat if sel.start - 5 <= t <= sel.end]
    preset = PostTexts(sel.card, sel.title, sel.description, sel.hashtags) if sel.by_ai else None
    if sel.by_ai:
        note(f"🧠 {streamer.display}: nota {sel.score:.0f} — {sel.reason}")
    return _finish(streamer, workdir, tr, max(0.5, peak_offset - sel.start), settings, platform=platform,
                   live_title=live_title, chat_snippets=snippets, preset=preset, t0=t0)


def rerender_card(clip_id: int, card_lines: list[str], settings: Settings) -> db.Clip:
    """Refaz o vídeo com um card editado no painel."""
    clip = db.get_clip(clip_id)
    if clip is None:
        raise KeyError(clip_id)
    workdir = settings.data_dir / clip.workdir
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    card = fit_card(card_lines)
    _render(workdir, meta, card, settings)
    db.update_clip(clip_id, card=card, created_at=clip.created_at)
    return db.get_clip(clip_id)
