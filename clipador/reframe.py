"""Enquadramento vertical automático: acha a área do jogo e segue a ação com uma "câmera" suave.

1. Lê o clipe em miniatura (160x90, cinza, 2 quadros/s).
2. Mede onde a imagem muda: bordas pretas, painel de chat e overlay parado ficam de fora da caixa do jogo;
   a webcam (se marcada) é ignorada.
3. Em cada instante, o centro da ação = centroide horizontal do movimento dentro da caixa.
4. Suaviza (média móvel), puxa um pouco pro centro, limita a velocidade da câmera e vira uma
   expressão de tempo do ffmpeg para o filtro crop.
Tudo em coordenadas do quadro 1280x720 (o render normaliza a entrada para esse tamanho).
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .recorder import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)
SRC_W, SRC_H = 1280, 720
AW, AH = 160, 90            # tamanho da análise
FPS = 2
MAX_PAN = 140               # px/s (no quadro 1280) — câmera não dá trancos
CENTER_PULL = 0.35          # 0 = segue a ação ao pé da letra, 1 = sempre no centro da caixa
SMOOTH_S = 3.0              # janela da média móvel


@dataclass
class Framing:
    box: tuple[int, int, int, int]          # área do jogo (x, y, w, h)
    keys: list[tuple[float, float]]         # (segundo, centro x da câmera)

    def x_at(self, t: float) -> float:
        k = self.keys
        if t <= k[0][0]:
            return k[0][1]
        for (t0, x0), (t1, x1) in zip(k, k[1:]):
            if t0 <= t <= t1:
                return x0 + (x1 - x0) * (t - t0) / ((t1 - t0) or 1)
        return k[-1][1]

    def at(self, t: float) -> "Framing":
        """Câmera parada no ponto do instante t (para a capa)."""
        return Framing(self.box, [(0.0, round(self.x_at(t), 1))])

    def to_json(self) -> dict:
        return {"box": list(self.box), "keys": [list(k) for k in self.keys]}

    @classmethod
    def from_json(cls, d: dict) -> "Framing":
        return cls(tuple(int(v) for v in d["box"]), [(float(t), float(x)) for t, x in d["keys"]])


def centered() -> Framing:
    return Framing((0, 0, SRC_W, SRC_H), [(0.0, SRC_W / 2)])


def read_frames(src: Path, fps: int = FPS, max_s: float = 130) -> list[bytes]:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-t", f"{max_s}", "-i", str(src),
           "-vf", f"fps={fps},scale={SRC_W}:{SRC_H}:force_original_aspect_ratio=increase,"
                  f"crop={SRC_W}:{SRC_H},scale={AW}:{AH}:flags=area,format=gray",
           "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, timeout=120)
    n = AW * AH
    data = r.stdout
    return [data[i:i + n] for i in range(0, len(data) - n + 1, n)]


def _runs(profile: list[float], thr: float, gap: int = 3) -> tuple[int, int]:
    """Maior trecho contínuo acima de thr (tolerando buracos de até `gap`). Devolve [ini, fim)."""
    best, cur_start, last_on = (0, 0), None, -10
    for i, v in enumerate(profile + [-1.0] * (gap + 1)):
        if v >= thr:
            if cur_start is None or i - last_on > gap + 1:
                if cur_start is not None and last_on + 1 - cur_start > best[1] - best[0]:
                    best = (cur_start, last_on + 1)
                cur_start = i
            last_on = i
    if cur_start is not None and last_on + 1 - cur_start > best[1] - best[0]:
        best = (cur_start, last_on + 1)
    return best


def _percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1)))] if s else 0.0


def plan(frames: list[bytes], w: int = AW, h: int = AH, fps: float = FPS,
         webcam: tuple[int, int, int, int] | None = None) -> Framing:
    if len(frames) < 3:
        return centered()
    sx, sy = SRC_W / w, SRC_H / h
    n = w * h
    mask = bytearray([1]) * n
    if webcam:
        cx0, cy0 = int(webcam[0] / sx), int(webcam[1] / sy)
        cx1, cy1 = int((webcam[0] + webcam[2]) / sx + 1), int((webcam[1] + webcam[3]) / sy + 1)
        for y in range(max(cy0, 0), min(cy1, h)):
            mask[y * w + max(cx0, 0): y * w + min(cx1, w)] = bytes(min(cx1, w) - max(cx0, 0))

    diffs = []
    act = [0] * n
    for a, b in zip(frames, frames[1:]):
        d = [abs(p - q) * m for p, q, m in zip(a, b, mask)]
        diffs.append(d)
        act = [x + y for x, y in zip(act, d)]

    col = [sum(act[y * w + x] for y in range(h)) / h for x in range(w)]
    row = [sum(act[y * w:(y + 1) * w]) / w for y in range(h)]
    top = _percentile(col, 0.8)
    if top < len(diffs) * 1.5:  # quase nada mexe (pausa, tela parada): centro simples
        return centered()
    x0, x1 = _runs(col, 0.2 * top)
    y0, y1 = _runs(row, 0.2 * _percentile(row, 0.8))
    if x1 - x0 < w * 0.4:
        x0, x1 = 0, w
    if y1 - y0 < h * 0.5:
        y0, y1 = 0, h
    box = (int(x0 * sx), int(y0 * sy), int((x1 - x0) * sx), int((y1 - y0) * sy))
    box_c = box[0] + box[2] / 2

    # centro da ação em cada passo
    raw = []
    for d in diffs:
        tot = wsum = 0.0
        for x in range(x0, x1):
            c = sum(d[y * w + x] for y in range(y0, y1))
            tot += c
            wsum += c * (x + 0.5)
        raw.append(wsum / tot * sx if tot > 0 else box_c)

    half = max(1, int(SMOOTH_S * fps / 2))
    smooth = [sum(raw[max(0, i - half):i + half + 1]) / len(raw[max(0, i - half):i + half + 1])
              for i in range(len(raw))]
    target = [box_c + (1 - CENTER_PULL) * (v - box_c) for v in smooth]

    keys: list[tuple[float, float]] = []
    step = 1.0 / fps
    x = target[0]
    for i, v in enumerate(target):
        t = (i + 0.5) * step
        if keys:
            dt = t - keys[-1][0]
            x = keys[-1][1] + max(-MAX_PAN * dt, min(MAX_PAN * dt, v - keys[-1][1]))
        keys.append((round(t, 2), round(x, 1)))
    # simplifica: tira pontos que não mudam quase nada
    simple = [keys[0]]
    for k in keys[1:-1]:
        if abs(k[1] - simple[-1][1]) >= 6:
            simple.append(k)
    if len(keys) > 1:
        simple.append(keys[-1])
    return Framing(box, simple)


def analyze(src: Path, webcam: tuple[int, int, int, int] | None = None) -> Framing:
    try:
        return plan(read_frames(src), webcam=webcam)
    except Exception:
        log.exception("enquadramento automático falhou — usando o centro")
        return centered()


def avoid_webcam(fr: Framing, webcam: tuple[int, int, int, int] | None, cw: int) -> Framing:
    """Tira da caixa do jogo o lado onde está a webcam, se ainda couber o recorte (cw de largura).
    Assim a câmera não aparece duas vezes (na faixa de cima e dentro do jogo)."""
    if not webcam:
        return fr
    bx, by, bw, bh = fr.box
    cx0, cx1 = webcam[0], webcam[0] + webcam[2]
    left_room, right_room = cx0 - bx, bx + bw - cx1
    if cx0 + cx1 > SRC_W and left_room >= cw:      # câmera no lado direito
        return Framing((bx, by, left_room, bh), fr.keys)
    if cx0 + cx1 <= SRC_W and right_room >= cw:    # câmera no lado esquerdo
        return Framing((cx1, by, right_room, bh), fr.keys)
    return fr


def crop_geometry(fr: Framing, aspect: float) -> tuple[int, int, int]:
    """Tamanho do recorte (w, h, y) com a proporção `aspect` (largura/altura) dentro da caixa do jogo."""
    bx, by, bw, bh = fr.box
    # apara a borda de cima/baixo do quadro: overlays da live costumam ter um fio de cor ali
    edge = 6
    if by == 0:
        by, bh = by + edge, bh - edge
    if by + bh >= SRC_H:
        bh -= edge
    ch = bh
    cw = int(round(ch * aspect))
    if cw > bw:
        cw = bw
        ch = int(round(cw / aspect))
    cw, ch = cw - cw % 2, ch - ch % 2
    return cw, ch, by + (bh - ch) // 2


def crop_x_expr(fr: Framing, cw: int) -> str:
    """Expressão do ffmpeg para o x do crop ao longo do tempo (já presa dentro da caixa)."""
    bx, _, bw, _ = fr.box
    lo, hi = bx, bx + bw - cw

    def left(cx: float) -> int:
        return int(max(lo, min(hi, cx - cw / 2)))

    pts = [(t, left(x)) for t, x in fr.keys]
    if len(pts) == 1 or all(p[1] == pts[0][1] for p in pts):
        return str(pts[0][1])
    terms = [f"lt(t,{pts[0][0]})*{pts[0][1]}"]
    for (t0, x0), (t1, x1) in zip(pts, pts[1:]):
        terms.append(f"gte(t,{t0})*lt(t,{t1})*({x0}+{x1 - x0}*(t-{t0})/{round(t1 - t0, 3)})")
    terms.append(f"gte(t,{pts[-1][0]})*{pts[-1][1]}")
    return "+".join(terms)
