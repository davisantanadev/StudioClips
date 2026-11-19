"""Clipar de live gravada (VOD): acha os picos pelo replay do chat (e/ou áudio) e baixa só os trechos."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings, Streamer
from .detector import HYPE_RE
from .pipeline import new_workdir, process_candidate
from .recorder import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)
BUCKET = 2.0
# yt-dlp escreve em UTF-8 (títulos com acento); sem isso, "é" vira "Ã©" no Windows
UTF8 = {"text": True, "encoding": "utf-8", "errors": "replace", "env": {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}}


@dataclass
class Moment:
    t: float      # segundos desde o início da VOD
    score: float


def platform_of(url: str) -> str:
    u = url.lower()
    return "twitch" if "twitch.tv" in u else "kick" if "kick.com" in u else "youtube" if "youtu" in u else "vod"


def parse_replay_line(line: str) -> list[tuple[float, str]]:
    """(segundo na VOD, texto) de uma linha do replay de chat do YouTube gravado pelo yt-dlp."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    a = obj.get("replayChatItemAction") if isinstance(obj, dict) else None
    if not isinstance(a, dict) or a.get("videoOffsetTimeMsec") is None:
        return []
    try:
        t = int(a["videoOffsetTimeMsec"]) / 1000
    except (TypeError, ValueError):
        return []
    out = []
    for act in a.get("actions") or []:
        r = (((act or {}).get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
        if not r:
            continue
        text = "".join(str(x.get("text", "")) if "text" in x else str((x.get("emoji") or {}).get("emojiId", ""))[:4]
                       for x in (r.get("message") or {}).get("runs") or []).strip()
        if text:
            out.append((t, text))
    return out


def _youtube_replay(url: str, progress, stop, total_s: float, workdir: Path | None) -> list[tuple[float, str]]:
    """Replay do chat de VOD do YouTube pelo yt-dlp (o chat-downloader quebrou em nov/2026).
    Ideia do gtavi-claudecode (MIT)."""
    import shutil
    import tempfile
    import time
    own = workdir is None
    folder = Path(tempfile.mkdtemp(prefix="clipador_vodchat_")) if own else Path(workdir)
    folder.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--sub-langs",
                             "live_chat", "-o", str(folder / "chat"), url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    from .chat.youtube import read_shared

    def read_all() -> list[tuple[float, str]]:
        msgs = []
        for f in sorted(folder.glob("chat.live_chat.json*")):
            if "Frag" in f.name:
                continue
            try:
                data = read_shared(f).decode("utf-8", "replace")
            except OSError:
                continue
            for line in data.splitlines():
                msgs += parse_replay_line(line)
        return msgs

    try:
        while proc.poll() is None:
            if stop():
                kill_tree(proc)
                break
            time.sleep(3)
            got = read_all()
            if got and total_s:
                progress(min(got[-1][0] / total_s, 1.0), len(got))
        return read_all()
    finally:
        if own:
            shutil.rmtree(folder, ignore_errors=True)


def vod_heatmap(url: str) -> list[dict]:
    """"Mais repetidos" do YouTube: [{start_time, end_time, value 0-1}]. Vazio se não tiver (VOD nova/pouco vista)."""
    if platform_of(url) != "youtube":
        return []
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "-J", "--skip-download", url], capture_output=True,
                       timeout=120, creationflags=NO_WINDOW, **UTF8)
    try:
        return list(json.loads(r.stdout).get("heatmap") or [])
    except (ValueError, AttributeError):
        return []


def heatmap_boost(scores: list[float], heat: list[dict], bucket: float = BUCKET, weight: float = 4.0) -> list[float]:
    """Soma pontos onde a galera mais reviu o vídeo. O trecho mais revisto ganha `weight` (passa do limite sozinho);
    o que está na média ou abaixo não ganha nada. Não altera `scores`."""
    out = list(scores)
    if not heat:
        return out
    vals = [0.0] * len(out)
    for h in heat:
        try:
            a, b, v = float(h["start_time"]), float(h["end_time"]), float(h["value"])
        except (KeyError, TypeError, ValueError):
            continue
        for i in range(int(a // bucket), min(len(out), int(math.ceil(b / bucket)))):
            vals[i] = max(vals[i], v)
    covered = sorted(v for v in vals if v > 0)
    if not covered:
        return out
    med, top = statistics.median(covered), covered[-1]
    if top <= med:
        return out
    for i, v in enumerate(vals):
        out[i] += weight * max(0.0, (v - med) / (top - med))
    return out


def chat_replay(url: str, progress: Callable[[float, int], None] = lambda frac, n: None,
                stop: Callable[[], bool] = lambda: False, total_s: float = 0.0,
                workdir: Path | None = None) -> list[tuple[float, str]]:
    """(segundo_na_vod, texto) de toda a VOD. YouTube pelo yt-dlp; Twitch pelo chat-downloader; Kick não tem.
    `progress(fração_da_vod_lida, mensagens)`; `stop()` verdadeiro interrompe (devolve o que leu até ali)."""
    if platform_of(url) == "youtube":
        try:
            msgs = _youtube_replay(url, progress, stop, total_s, workdir)
            if msgs:
                return msgs
        except Exception as e:
            log.warning("replay de chat do YouTube (yt-dlp) falhou: %s", e)
    try:
        from chat_downloader import ChatDownloader
    except ImportError:
        return []
    out = []
    try:
        # poucas tentativas: se o site mudou a API (aconteceu com a Twitch), falha em segundos e usa o áudio
        for i, m in enumerate(ChatDownloader().get_chat(url, message_types=["text_message"], max_attempts=3,
                                                        retry_timeout=1)):
            t = m.get("time_in_seconds")
            if t is not None:
                out.append((float(t), str(m.get("message", ""))))
            if i % 500 == 0:
                if stop():
                    break
                if i % 2000 == 0 and out:
                    progress(min(out[-1][0] / total_s, 1.0) if total_s else 0.0, len(out))
    except Exception as e:
        log.warning("replay de chat indisponível: %s", e)
    return out


def _zscores(xs: list[float], window: int = 150) -> list[float]:
    """z-score de cada ponto contra a mediana/MAD da vizinhança (±5 min)."""
    zs = []
    for i, x in enumerate(xs):
        w = xs[max(0, i - window): i + window]
        med = statistics.median(w)
        mad = statistics.median(abs(v - med) for v in w) * 1.4826
        sd = max(mad, 0.35 * med, 0.5)
        zs.append((x - med) / sd)
    return zs


def chat_scores(msgs: list[tuple[float, str]], total_s: float) -> list[float]:
    n = int(math.ceil(total_s / BUCKET)) + 1
    cnt, hype = [0] * n, [0] * n
    for t, text in msgs:
        i = min(int(t // BUCKET), n - 1)
        cnt[i] += 1
        if HYPE_RE.search(text):
            hype[i] += 1
    # taxa sustentada (janela de 3 baldes)
    rate = [sum(cnt[max(0, i - 2): i + 1]) / 3 for i in range(n)]
    zs = _zscores(rate)
    for i in range(n):
        h = sum(hype[max(0, i - 2): i + 1])
        c = sum(cnt[max(0, i - 2): i + 1])
        if c >= 6 and h / c >= 0.3:
            zs[i] += 1.0
    return zs


def pick_moments(scores: list[float], top_n: int, min_gap_s: float = 90, threshold: float = 3.5,
                 skip_start_s: float = 60) -> list[Moment]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    chosen: list[Moment] = []
    for i in order:
        t = (i + 0.5) * BUCKET - 3.0  # reação do chat vem ~3 s depois
        if scores[i] < threshold or len(chosen) >= top_n:
            break
        if t < skip_start_s or any(abs(t - m.t) < min_gap_s for m in chosen):
            continue
        chosen.append(Moment(t, round(scores[i], 2)))
    return sorted(chosen, key=lambda m: m.t)


def vod_info(url: str) -> tuple[float, str, str]:
    """(duração em s, título, live_status) numa consulta só. live_status 'is_live' = a live ainda está no ar."""
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--print",
                        "%(duration)s\t%(live_status)s\t%(title)s", url],
                       capture_output=True, **UTF8, creationflags=NO_WINDOW)
    line = (r.stdout.strip().splitlines() or [""])[-1]
    parts = line.split("\t", 2)
    try:
        dur = float(parts[0])
    except (ValueError, IndexError):
        dur = 0.0
    return dur, (parts[2] if len(parts) > 2 else ""), (parts[1] if len(parts) > 1 else "")


def vod_duration(url: str) -> float:
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--print", "duration", "--skip-download", url],
                       capture_output=True, **UTF8, creationflags=NO_WINDOW)
    try:
        return float(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0.0


def vod_title(url: str) -> str:
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--print", "title", "--skip-download", url],
                       capture_output=True, **UTF8, creationflags=NO_WINDOW)
    return (r.stdout.strip().splitlines() or [""])[-1]


class Stopped(Exception):
    """Pausa/exclusão pedida no meio de um download."""


def kill_tree(proc: subprocess.Popen) -> None:
    """Mata o processo E os filhos dele (o yt-dlp às vezes abre um ffmpeg que segura o arquivo)."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=NO_WINDOW)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def audio_scores(url: str, workdir: Path, total_s: float, progress: Callable[[float], None] = lambda f: None,
                 stop: Callable[[], bool] = lambda: False) -> list[float]:
    """Sem chat: baixa só o áudio (leve) e usa o volume como sinal (grito, risada alta, explosão).
    `progress(fração)` acompanha o download; `stop()` verdadeiro cancela na hora (levanta Stopped)."""
    audio = workdir / "vod_audio.m4a"
    audio.unlink(missing_ok=True)
    # %(ext)s: o yt-dlp baixa no formato original e o -x converte para vod_audio.m4a
    # baixador próprio do yt-dlp pra HLS (Twitch): mostra a porcentagem; o do ffmpeg não mostra
    proc = subprocess.Popen([sys.executable, "-m", "yt_dlp", "--newline", "--progress", "--downloader",
                             "m3u8:native", "-f", "bestaudio/worst", "-x", "--audio-format", "m4a",
                             "-o", str(workdir / "vod_audio.%(ext)s"), url],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **UTF8,
                            creationflags=NO_WINDOW)
    for line in proc.stdout:
        if stop():
            kill_tree(proc)
            for f in workdir.glob("vod_audio.*"):
                try:
                    f.unlink(missing_ok=True)
                except OSError:  # ainda preso por um instante: some com a pasta temporária depois
                    pass
            raise Stopped()
        m = re.search(r"\[download\]\s+([\d.]+)%", line)
        if m:
            progress(float(m.group(1)) / 100)
            continue
        # VOD da Twitch: o yt-dlp passa o download pro ffmpeg, que só diz quanto TEMPO de áudio já baixou
        m = re.search(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", line)
        if m and total_s > 0:
            done = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            progress(min(done / total_s, 1.0))
    if proc.wait() != 0:
        raise RuntimeError("não consegui baixar o áudio da VOD")
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(audio), "-af",
                        f"aresample=8000,asetnsamples={int(8000 * BUCKET)},astats=metadata=1:reset=1,"
                        "ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    vals = [float(v) for v in re.findall(r"RMS_level=(-?[\d.]+|-inf)", r.stderr.replace("-inf", "-120"))]
    audio.unlink(missing_ok=True)
    return _zscores(vals) if vals else [0.0] * int(total_s // BUCKET)


def download_section(url: str, start: float, end: float, out: Path) -> Path:
    # 1080p: a webcam recortada sai nítida (como nas lives gravadas em HD)
    subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
                    "--download-sections", f"*{max(start, 0):.1f}-{end:.1f}", "--force-keyframes-at-cuts",
                    "--merge-output-format", "mp4", "-o", str(out), url], check=True, creationflags=NO_WINDOW)
    if not out.exists():  # yt-dlp às vezes troca a extensão
        cand = sorted(out.parent.glob(out.stem + ".*"))
        if cand:
            cand[0].rename(out)
    return out


def clip_vod(url: str, streamer: Streamer, settings: Settings, top_n: int = 6,
             progress: Callable[[str], None] = log.info) -> int:
    """Gera até `top_n` clipes de uma VOD. Retorna quantos ficaram prontos."""
    progress(f"🎞️ analisando VOD de {streamer.display}…")
    total = vod_duration(url)
    if total <= 0:
        raise RuntimeError("não consegui ler a VOD (link certo? ela é pública?)")
    title = vod_title(url)
    msgs = chat_replay(url, lambda f, n: progress(f"lendo chat da VOD: {n} mensagens…"), total_s=total)
    tmp = settings.data_dir / "vod_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    if len(msgs) > 200:
        scores = chat_scores(msgs, total)
        progress(f"chat da VOD lido ({len(msgs)} mensagens)")
    else:
        progress("sem replay de chat — usando o volume do áudio")
        scores = audio_scores(url, tmp, total)
    heat = vod_heatmap(url)
    if heat:
        scores = heatmap_boost(scores, heat)
        progress("🔁 \"Mais repetidos\" do YouTube entra como sinal")
    from .twitchclips import hits_near, vod_boost
    scores, hits = vod_boost(url, scores, BUCKET, note=progress)
    moments = pick_moments(scores, top_n * 2)  # pega o dobro: a IA descarta os fracos
    if not moments:
        progress("nenhum pico forte encontrado nessa VOD")
        return 0
    progress(f"{len(moments)} momentos candidatos, a IA vai escolher os melhores…")
    before, after = settings.analysis_before_s, settings.analysis_after_s
    done = 0
    for k, m in enumerate(sorted(moments, key=lambda x: -x.score), 1):
        if done >= top_n:
            break
        start, end = max(0.0, m.t - before), min(total, m.t + after)
        try:
            wd = new_workdir(settings, streamer)
            raw = download_section(url, start, end, wd / "analysis.mp4")
            chat = [(x - start, t) for (x, t) in msgs if start <= x <= end]
            n_clips, views = hits_near(hits, m.t)
            clip = process_candidate(streamer, raw, m.t - start, settings, platform=platform_of(url),
                                     live_title=title, chat=chat, workdir=wd, note=progress,
                                     signals={"twitch_clips": n_clips, "twitch_views": views} if n_clips else None)
            if clip:
                done += 1
                progress(f"✅ VOD {streamer.display}: {done}/{top_n} pronto")
        except Exception as e:
            log.exception("VOD")
            progress(f"❌ VOD {streamer.display}: trecho {k} falhou ({e})")
    return done
