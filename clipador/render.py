"""Monta o vertical 9:16:

- webcam (achada sozinha ou marcada) em cima, o jogo logo abaixo;
- o "zoom" escolhe quanto do jogo aparece: aberto (mostra mais), médio ou fechado (jogo preenche tudo);
- o que sobra em cima/embaixo é o próprio jogo desfocado, então a tela nunca fica preta;
- o recorte do jogo segue a ação (reframe.py) e foge da webcam pra ela não aparecer duas vezes.
O card entra na emenda webcam/jogo nos primeiros segundos e some.
"""
from __future__ import annotations

import functools
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .config import RES_DIR
from .recorder import NO_WINDOW, ffmpeg_bin
from .reframe import Framing, analyze, avoid_webcam, crop_geometry, crop_x_expr

FONTS = RES_DIR / "assets" / "fonts"
CARD_FONT = FONTS / "ArchivoBlack-Regular.ttf"
W, H = 1080, 1920
CARD_H = 260
RENDER_TIMEOUT_S = 900   # montar um clipe de até 90 s leva ~1-2 min; 15 min = travou
COVER_TIMEOUT_S = 120
# proporção (largura/altura) do pedaço do jogo; None = preenche todo o espaço que sobra
ZOOMS: dict[str, float | None] = {"aberto": 1.25, "medio": 1.0, "fechado": None}
DEFAULT_ZOOM = "aberto"


@dataclass
class Layout:
    cam_y: int
    cam_h: int      # 0 = sem webcam
    game_y: int
    game_h: int

    @property
    def seam(self) -> int:  # onde o card fica: emenda webcam/jogo (ou topo do jogo)
        return self.game_y if self.cam_h or self.game_y else 330

    @property
    def sub_y(self) -> int:  # legenda no terço de baixo do jogo, acima da interface do TikTok
        return min(self.game_y + int(self.game_h * 0.80), 1560)

    @property
    def padded(self) -> bool:
        return self.cam_h + self.game_h < H


def _even(v: float) -> int:
    v = int(round(v))
    return v - v % 2


def layout(webcam: tuple[int, int, int, int] | None, zoom: str = DEFAULT_ZOOM) -> Layout:
    aspect = ZOOMS.get(zoom, ZOOMS[DEFAULT_ZOOM])
    cam_h = _even(min(max(W * webcam[3] / webcam[2], 520), 640)) if webcam else 0
    room = H - cam_h
    game_h = room if aspect is None else min(room, _even(W / aspect))
    free = H - cam_h - game_h
    top = _even(free * (0.32 if webcam else 0.42))
    return Layout(cam_y=top, cam_h=cam_h, game_y=top + cam_h, game_h=game_h)


def subtitle_y(webcam: tuple[int, int, int, int] | None, zoom: str = DEFAULT_ZOOM) -> int:
    return layout(webcam, zoom).sub_y


def _rel(p: Path, base: Path) -> str:
    try:
        rel = Path(os.path.relpath(p, base)).as_posix()
        if rel.count("../") <= 3:  # caminho longo cheio de ".." o ffmpeg às vezes não acha: usa o absoluto
            return rel
    except ValueError:  # outro disco no Windows
        pass
    return Path(p).resolve().as_posix().replace(":", "\\:")


def _hex(c: str) -> str:
    return "0x" + c.lstrip("#")


@functools.lru_cache(maxsize=1)
def _has_nvenc() -> bool:
    try:
        test = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                              capture_output=True, creationflags=NO_WINDOW, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return test.returncode == 0


def video_encoder(fast: bool = False) -> list[str]:
    """Usa a NVENC da placa NVIDIA se existir (GTX 1650 tem); senão x264.
    fast=True: corte intermediário (qualidade alta, velocidade máxima)."""
    if _has_nvenc():
        return ["-c:v", "h264_nvenc", "-preset", "p1" if fast else "p4", "-rc", "vbr",
                "-cq", "18" if fast else "20", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "ultrafast" if fast else "veryfast", "-crf", "17" if fast else "20"]


def has_audio(path: Path) -> bool:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=index", "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                           creationflags=NO_WINDOW, timeout=60)
    except subprocess.TimeoutExpired:
        return False
    return bool(r.stdout.strip())


def probe_duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(path)], capture_output=True, text=True, creationflags=NO_WINDOW, timeout=60)
        return float(r.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


def sample_frames(src: Path, n: int = 6, width: int = 512) -> list[bytes]:
    """`n` quadros JPEG espalhados pelo vídeo (para a IA "ver" o clipe)."""
    dur = probe_duration(src)
    if dur <= 0:
        return []
    out = []
    for i in range(n):
        at = dur * (0.06 + 0.88 * i / max(n - 1, 1))
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2pipe",
                            "-vcodec", "mjpeg", "-"], capture_output=True, creationflags=NO_WINDOW, timeout=30)
        if r.returncode == 0 and r.stdout:
            out.append(r.stdout)
    return out


CARD_WRAP = 24  # letras por linha desenhada: linha comprida vira duas, com fonte grande


def card_rows(lines: list[str]) -> list[tuple[str, int]]:
    """Quebra cada linha do card em linhas desenhadas curtas. Devolve (texto, linha de origem)."""
    rows = []
    for i, line in enumerate(lines):
        subs = textwrap.wrap(line, CARD_WRAP) if len(line) > CARD_WRAP + 4 else [line]
        rows += [(s, i) for s in subs]
    return rows[:4]


def card_font_size(lines: list[str]) -> int:
    longest = max((len(l) for l in lines), default=1)
    # card em MAIÚSCULA (Archivo Black ~0,8 da altura por letra): cabe em 1000 px com folga
    return int(max(44, min(72, 1000 / (0.8 * max(longest, 1)))))


def card_height(lines: list[str]) -> int:
    rows = [r for r, _ in card_rows(lines)]
    size = card_font_size(rows)
    gap = int(size * 1.2)
    return max(CARD_H, gap * len(rows) - (gap - size) + 2 * 44)


def _card_stream(lines: list[str], workdir: Path, accent: str, card_s: float | None) -> str:
    """Card como camada própria (RGBA): entra suave e, se card_s, desliza pra cima e some.
    1ª frase em branco, 2ª (a virada) na cor de destaque."""
    font = _rel(CARD_FONT, workdir)
    rows = card_rows(lines)
    size = card_font_size([r for r, _ in rows])
    gap = int(size * 1.2)
    ch = card_height(lines)
    block = gap * len(rows) - (gap - size)
    top = (ch - block) // 2
    f = [f"color=c=0x0E0E12:s={W}x{ch}:r=30", "format=rgba",
         f"drawbox=x=0:y=0:w={W}:h=8:color={_hex(accent)}:t=fill",
         f"drawbox=x=0:y={ch - 4}:w={W}:h=4:color={_hex(accent)}@0.6:t=fill"]
    for k, (text, src_line) in enumerate(rows):
        tf = workdir / f"card_{k}.txt"
        tf.write_text(text.upper(), encoding="utf-8")
        color = _hex(accent) if src_line == 1 else "white"
        f.append(f"drawtext=fontfile='{font}':textfile='{tf.name}':fontsize={size}:fontcolor={color}"
                 f":borderw=2:bordercolor=black@0.6:expansion=none:x=(w-text_w)/2:y={top + k * gap}")
    if card_s is not None:
        f.append("fade=t=in:st=0:d=0.3:alpha=1")
        f.append(f"fade=t=out:st={card_s:.2f}:d=0.45:alpha=1")
    return ",".join(f) + "[card]"


CAM_FIT_BELOW = 1.45  # câmera mais "quadrada" que isso: mostra inteira (sem cortar o rosto) com laterais desfocadas


# O render trabalha na imagem cheia (1920x1080): a live gravada em 1080p não perde resolução antes do
# recorte. Posições (webcam, enquadramento) continuam na grade 1280x720 e são multiplicadas por K aqui.
BW, BH = 1920, 1080
K = BW / 1280


def _k(v: float) -> int:
    v = int(round(v * K))
    return v - v % 2


def cam_band(webcam: tuple[int, int, int, int], cam_h: int) -> str:
    """Faixa da webcam (W x cam_h) a partir do ramo [a], recortada da imagem cheia.
    Antes de ampliar: tira o ruído/quadradinhos da compressão da live (hqdn3d); depois amplia com lanczos e
    devolve nitidez. Câmera larga preenche a faixa; quase quadrada aparece inteira com laterais desfocadas."""
    x, y, w, h = (_k(v) for v in webcam)
    clean = "hqdn3d=1.5:1.5:3:3"
    sharp = "unsharp=5:5:0.8:3:3:0.3"
    if w / h >= CAM_FIT_BELOW:
        return (f"[a]crop={w}:{h}:{x}:{y},{clean},scale={W}:{cam_h}:force_original_aspect_ratio=increase"
                f":flags=lanczos,crop={W}:{cam_h},{sharp},setsar=1[cam]")
    return (f"[a]crop={w}:{h}:{x}:{y},{clean},split=2[ca][cb];"
            f"[ca]scale=270:{cam_h // 4}:force_original_aspect_ratio=increase,crop=270:{cam_h // 4},boxblur=6:2,"
            f"scale={W}:{cam_h},eq=brightness=-0.25,setsar=1[cbg];"
            f"[cb]scale=-2:{cam_h}:flags=lanczos,{sharp},setsar=1[cfg];"
            f"[cbg][cfg]overlay=(W-w)/2:0[cam]")


def game_crop(framing: Framing, out_w: int, out_h: int) -> str:
    """Recorte em pé do jogo que segue a ação, ampliado com nitidez para out_w x out_h."""
    cw, ch, cy = crop_geometry(framing, out_w / out_h)
    x = crop_x_expr(framing, cw)
    return (f"crop=w={_k(cw)}:h={_k(ch)}:x='trunc({K}*({x})/2)*2':y={_k(cy)},"
            f"scale={out_w}:{out_h}:flags=lanczos,unsharp=5:5:0.7:5:5:0")


def build_filter(webcam: tuple[int, int, int, int] | None, card: list[str], ass: Path | None,
                 workdir: Path, handle: str, accent: str, duration: float, audio: bool,
                 card_s: float | None = 4.0, progress: bool = True, framing: Framing | None = None,
                 zoom: str = DEFAULT_ZOOM) -> str:
    from .reframe import centered
    lay = layout(webcam, zoom)
    framing = framing or centered()
    cw = crop_geometry(framing, W / lay.game_h)[0]
    framing = avoid_webcam(framing, webcam, cw)
    branches = (["a"] if webcam else []) + ["b"] + (["c"] if lay.padded else [])
    src = (f"[0:v]scale={BW}:{BH}:force_original_aspect_ratio=increase:flags=lanczos,crop={BW}:{BH},"
           "setsar=1,fps=30")
    parts = [src + (f",split={len(branches)}" + "".join(f"[{b}]" for b in branches) if len(branches) > 1
                    else f"[{branches[0]}]")]
    parts.append(f"[b]{game_crop(framing, W, lay.game_h)}[game]")
    if lay.padded:  # fundo: o próprio jogo desfocado (desfoca pequeno e amplia: bem mais leve)
        parts.append(f"[c]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
                     f"scale={W}:{H},eq=brightness=-0.22:saturation=1.15,setsar=1[bg]")
    else:
        parts.append(f"color=c=black:s={W}x{H}:r=30[bg]")
    if webcam:
        parts.append(cam_band(webcam, lay.cam_h))
        parts.append(f"[bg][cam]overlay=0:{lay.cam_y}:shortest=1[t1]")
        parts.append(f"[t1][game]overlay=0:{lay.game_y}:shortest=1[base]")
    else:
        parts.append(f"[bg][game]overlay=0:{lay.game_y}:shortest=1[base]")
    card_y = max(0, lay.seam - card_height(card) // 2) if card else 0
    has_card = bool(card)
    if has_card:
        parts.append(_card_stream(card, workdir, accent, card_s))
        ycard = f"{card_y}+28*max(0,1-t/0.3)"
        if card_s is not None:
            ycard += f"-max(0,t-{card_s:.2f})*220"
        parts.append(f"[base][card]overlay=x=0:y='{ycard}':shortest=1[stk]")
    else:
        parts.append("[base]null[stk]")
    chain = []
    if handle:  # marca d'água fixa (o card some, o @ fica)
        hf = workdir / "handle.txt"
        hf.write_text(handle, encoding="utf-8")
        chain.append(f"drawtext=fontfile='{_rel(CARD_FONT, workdir)}':textfile='{hf.name}':fontsize=30"
                     f":fontcolor=white@0.75:borderw=2:bordercolor=black@0.5:expansion=none"
                     f":x=(w-text_w)/2:y={H - 78}")
    if ass is not None:
        chain.append(f"ass='{_rel(ass, workdir)}':fontsdir='{_rel(FONTS, workdir)}'")
    chain = chain or ["null"]
    d = max(duration, 0.1)
    if progress:
        parts.append("[stk]" + ",".join(chain) + "[pre]")
        # barra de progresso: drawbox não conhece o tempo, então desliza uma faixa com overlay
        parts.append(f"color=c={_hex(accent)}:s={W}x10:r=30[bar]")
        parts.append(f"[pre][bar]overlay=x='-W+W*t/{d:.3f}':y={H - 10}:shortest=1,format=yuv420p[v]")
    else:
        parts.append("[stk]" + ",".join(chain) + ",format=yuv420p[v]")
    if audio:
        parts.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    return ";\n".join(parts)


def render_vertical(src: Path, out: Path, webcam: tuple[int, int, int, int] | None, card: list[str],
                    ass: Path | None, handle: str, accent: str, card_s: float | None = 4.0,
                    framing: Framing | None = None, zoom: str = DEFAULT_ZOOM) -> Path:
    src, out = Path(src).resolve(), Path(out).resolve()
    workdir = out.parent
    workdir.mkdir(parents=True, exist_ok=True)
    dur = probe_duration(src)
    audio = has_audio(src)
    framing = framing or analyze(src, webcam)
    graph = build_filter(webcam, card, ass, workdir, handle, accent, dur, audio, card_s, framing=framing,
                         zoom=zoom)
    # inline: -filter_complex_script foi descontinuado no FFmpeg 7+ (o winget instala o mais novo)
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-filter_complex", graph.replace(";\n", ";"), "-map", "[v]"]
    cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"] if audio else []
    cmd += video_encoder() + ["-t", f"{dur:.3f}", "-r", "30", "-movflags", "+faststart", str(out)]
    try:  # prazo: um ffmpeg travado nunca mais segura a fila (antes travou tudo por mais de 1 h)
        r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, creationflags=NO_WINDOW,
                           timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg passou de {RENDER_TIMEOUT_S // 60} min montando o vídeo — desisti desse clipe")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {r.stderr[-1500:]}")
    return out


def make_cover(src: Path, at_s: float, out: Path, webcam: tuple[int, int, int, int] | None = None,
               card: list[str] | None = None, handle: str = "", accent: str = "#FF2E63",
               framing: Framing | None = None, zoom: str = DEFAULT_ZOOM) -> Path:
    """Capa = quadro do momento do pico, no mesmo layout, com o card sempre visível."""
    src, out = Path(src).resolve(), Path(out).resolve()
    workdir = out.parent
    # o quadro tem que existir: se a IA cortou o trecho antes do pico, o tempo caía depois do fim do vídeo e o
    # ffmpeg ficava pra sempre esperando um quadro que não vem (o fundo do card é infinito)
    dur = probe_duration(src)
    at_s = min(max(at_s, 0.0), max(dur - 0.5, 0.0)) if dur > 0 else 0.0
    still = framing.at(at_s) if framing else None  # com -ss o tempo recomeça do zero: câmera fixa no pico
    graph = build_filter(webcam, card or [], None, workdir, handle, accent, 1.0, False,
                         card_s=None, progress=False, framing=still, zoom=zoom)
    graph = graph.replace("+28*max(0,1-t/0.3)", "")
    try:
        r = subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}",
                            "-i", str(src), "-filter_complex", graph.replace(";\n", ";"), "-map", "[v]",
                            "-frames:v", "1", "-q:v", "2", str(out)],
                           cwd=workdir, capture_output=True, text=True, creationflags=NO_WINDOW,
                           timeout=COVER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError("capa passou do tempo")
    if r.returncode != 0:
        raise RuntimeError(f"capa falhou: {r.stderr[-800:]}")
    return out
