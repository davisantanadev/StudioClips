"""Acha a webcam do streamer sozinho, sem ninguém marcar nada.

1. Pega 12 quadros espalhados pelo clipe e procura rostos (YuNet, modelo do OpenCV, roda no PC).
2. Rosto de personagem do jogo se mexe; o rosto da webcam fica parado no mesmo lugar. Fica o rosto que
   aparece no mesmo ponto na maior parte dos quadros (e junta quem estiver do lado: câmera com 2 pessoas).
3. A caixa da câmera encosta nas bordas fixas da moldura (linhas que não mudam enquanto o jogo muda).
   Sem moldura (fundo verde), usa cabeça e ombros.
Coordenadas no quadro 1280x720 (o mesmo que o render usa).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import RES_DIR
from .recorder import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)
MODEL = RES_DIR / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
SW, SH = 1280, 720
N_FRAMES = 12
MIN_SHARE = 0.4  # o rosto precisa aparecer no mesmo lugar em pelo menos 40% dos quadros
FULLSCREEN_FACE = 0.22  # rosto maior que isso (fração da altura) = câmera em tela cheia
FULLSCREEN_AREA = 0.28  # caixa da câmera maior que isso (fração da tela) = câmera em tela cheia
BORDER_IN = 5           # px pra dentro da moldura (não mostrar a borda da câmera)
SMALL_CAM_W = 200       # câmera mais estreita que isso sai borrada ao ampliar pra 1080

Rect = tuple[int, int, int, int]


def available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return MODEL.exists()


def read_frames(src: Path, dur: float, n: int = N_FRAMES):
    """n quadros BGR 1280x720 espalhados pelo vídeo."""
    import numpy as np
    frames = []
    for i in range(n):
        at = dur * (0.04 + 0.92 * i / max(n - 1, 1))
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(src),
                            "-frames:v", "1", "-vf", f"scale={SW}:{SH}:force_original_aspect_ratio=increase,"
                            f"crop={SW}:{SH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           capture_output=True, creationflags=NO_WINDOW, timeout=30)
        if r.returncode == 0 and len(r.stdout) == SW * SH * 3:
            frames.append(np.frombuffer(r.stdout, np.uint8).reshape(SH, SW, 3))
    return frames


def detect_faces(frames) -> list[list[tuple[float, float, float, float, float]]]:
    import cv2
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (SW, SH), 0.6, 0.3, 50)
    out = []
    for f in frames:
        _, faces = det.detect(f)
        out.append([tuple(float(v) for v in (r[0], r[1], r[2], r[3], r[14])) for r in (faces if faces is not None else [])
                    if r[2] >= 12])
    return out


def persistent_face(dets: list[list[tuple]], n_frames: int) -> tuple[Rect, float] | None:
    """Rosto que fica parado: agrupa por posição e fica com o grupo presente em mais quadros.
    Devolve (caixa de todos os rostos parados vizinhos, altura do rosto principal)."""
    clusters: list[dict] = []
    for fi, faces in enumerate(dets):
        for x, y, w, h, s in faces:
            cx, cy = x + w / 2, y + h / 2
            best = None
            for c in clusters:
                mw = c["w"]
                if abs(cx - c["cx"]) < 0.6 * mw and abs(cy - c["cy"]) < 0.6 * mw and 0.6 < w / mw < 1.6:
                    best = c
                    break
            if best is None:
                clusters.append({"cx": cx, "cy": cy, "w": w, "frames": {fi}, "boxes": [(x, y, w, h)]})
            else:
                best["frames"].add(fi)
                best["boxes"].append((x, y, w, h))
                k = len(best["boxes"])
                best["cx"] += (cx - best["cx"]) / k
                best["cy"] += (cy - best["cy"]) / k
                best["w"] += (w - best["w"]) / k
    need = max(3, int(MIN_SHARE * n_frames + 0.5))
    good = [c for c in clusters if len(c["frames"]) >= need]
    if not good:
        return None

    def edge_dist(c):  # webcam costuma ficar perto de uma borda
        return min(c["cx"], SW - c["cx"], c["cy"], SH - c["cy"])

    main = max(good, key=lambda c: (len(c["frames"]), -edge_dist(c)))
    group = [c for c in good if abs(c["cx"] - main["cx"]) < 4 * main["w"] and abs(c["cy"] - main["cy"]) < 2 * main["w"]]

    def med(vals):
        s = sorted(vals)
        return s[len(s) // 2]

    boxes = []
    for c in group:
        bx = c["boxes"]
        boxes.append((med([b[0] for b in bx]), med([b[1] for b in bx]), med([b[2] for b in bx]), med([b[3] for b in bx])))
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    main_h = med([b[3] for b in main["boxes"]])
    return (int(x0), int(y0), int(x1 - x0), int(y1 - y0)), float(main_h)


def _snap(profile, lo: int, hi: int, prefer_far: bool) -> int | None:
    """Posição da linha fixa mais forte em [lo, hi) (moldura da câmera), se ela se destacar."""
    import numpy as np
    lo, hi = max(lo, 0), min(hi, len(profile))
    if hi - lo < 4:
        return None
    seg = profile[lo:hi]
    base = float(np.median(seg)) + 1e-6
    peak = float(seg.max())
    if peak < 9 or peak < 3.0 * base:
        return None
    strong = np.where(seg >= 0.7 * peak)[0]
    idx = strong[0] if prefer_far else strong[-1]
    return lo + int(idx)


CAM_ASPECT = 1.75   # proporção da faixa da webcam no vídeo final (1080 x ~616)


def head_and_shoulders(face: Rect, main_h: float) -> tuple[float, float, float, float]:
    """Enquadramento de cabeça e ombros proporcional ao rosto, dentro da tela (desliza, não encolhe)."""
    fx, fy, fw, fh = face
    s = main_h
    w = max(fw + 2.6 * s, 4.0 * s)
    h = w / CAM_ASPECT
    cx, cy = fx + fw / 2, fy + fh / 2
    left, top = cx - w / 2, cy - 0.42 * h
    w, h = min(w, SW), min(h, SH)
    left = min(max(left, 0.0), SW - w)
    top = min(max(top, 0.0), SH - h)
    return left, top, left + w, top + h


def cam_box(frames, face: Rect, main_h: float | None = None) -> Rect:
    """Caixa da câmera: cabeça e ombros em volta do rosto. Se a moldura da câmera aparecer DENTRO
    dessa caixa, encolhe até ela (pra não pegar o jogo em volta). Nunca cresce atrás de linhas."""
    import numpy as np
    fx, fy, fw, fh = face
    left, top, right, bottom = head_and_shoulders(face, main_h or fh)

    gray = np.stack([f.mean(axis=2) for f in frames]).astype(np.float32)
    gx = np.abs(np.diff(gray, axis=2)).mean(axis=0)        # linhas verticais fixas
    gy = np.abs(np.diff(gray, axis=1)).mean(axis=0)        # linhas horizontais fixas
    ys = slice(int(top), int(bottom))
    xs = slice(int(left), int(right))
    vprof = gx[ys].mean(axis=0)
    hprof = gy[:, xs].mean(axis=1)
    m = 0.35 * fw  # folga em volta do rosto
    lx = _snap(vprof, int(left) + 3, int(fx - m), prefer_far=False)
    rx = _snap(vprof, int(fx + fw + m), int(right) - 3, prefer_far=True)
    ty = _snap(hprof, int(top) + 3, int(fy - m), prefer_far=False)
    by = _snap(hprof, int(fy + fh + m), int(bottom) - 3, prefer_far=True)
    # achou a moldura: entra um pouco pra mostrar só a imagem da câmera, sem a borda colorida
    if lx is not None:
        left = lx + BORDER_IN
    if rx is not None:
        right = rx - BORDER_IN
    if ty is not None:
        top = ty + BORDER_IN
    if by is not None:
        bottom = by - BORDER_IN
    w, h = int(right - left), int(bottom - top)
    w, h = w - w % 2, h - h % 2
    return int(left), int(top), max(w, 16), max(h, 16)


def detect_webcam(src: Path, dur: float) -> Rect | None:
    """Caixa da webcam no clipe (1280x720), ou None se não achar uma com segurança."""
    if not available():
        return None
    try:
        frames = read_frames(src, dur)
        if len(frames) < 6:
            return None
        found = persistent_face(detect_faces(frames), len(frames))
        if found is None:
            return None
        face, main_h = found
        if main_h > FULLSCREEN_FACE * SH:  # câmera em tela cheia (conversa, sem jogo): sem faixa separada
            return None
        box = cam_box(frames, face, main_h)
        if box[2] * box[3] > FULLSCREEN_AREA * SW * SH:
            return None
        if box[2] < SMALL_CAM_W:
            log.info("câmera pequena na live (%d px de largura): vai sair um pouco borrada no vertical", box[2])
        return box
    except Exception:
        log.exception("detecção de webcam falhou")
        return None
