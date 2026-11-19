"""Detector de momento viral: chat acelerando COM reação (risada, susto/choque) ou o streamer gritando.

Chat rápido sem emoção (enxurrada de sub, comando de bot, "boa noite") não dispara: isso vira clipe chato.
Compara tudo com a média do próprio canal, então funciona em chat pequeno e em chat gigante.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import deque

LAUGH_RE = re.compile(
    r"(k{3,}|KEKW|LULW?|OMEGALUL|ICANT|LMAO+|LMFAO|ROFL|\bLOL\b|ha(ha)+|a?ha(ha)+|ja(ja)+|xD+|rsrs|"
    r"[хx]а[хx]а|ахах|😂|🤣|💀|kek)", re.I)
SHOCK_RE = re.compile(
    r"(WTF|OMG|\?{3,}|monka[SW]|\bD:|NO{3,}|N[ÃA]O{3,}|socorro|medo|susto|assust|jumpscare|"
    r"caralho|que isso|mds\b|meu deus|pog(gers|u)?|siu+|\bgg\b|clip\s*it|holy|insane|bruh|"
    r"😱|😨|😮|🤯|😳)", re.I)
NOISE_RE = re.compile(
    r"^\s*!|\bsubs?\b|subscri|inscri|presente|gift|\bbits?\b|raid|obrigad[oa] pel[oa]|valeu pel[oa]|"
    r"follow|seguindo|prime", re.I)
HYPE_RE = re.compile(f"{LAUGH_RE.pattern}|{SHOCK_RE.pattern}", re.I)  # VOD
BUCKET_S = 2.0
SCREAM_Z = 2.5          # volume da fala/jogo bem acima do normal do canal
LAUGH_SHARE = 0.20      # parte do chat rindo pra contar como "momento engraçado"
SHOCK_SHARE = 0.15


def classify(text: str) -> str:
    """'laugh', 'shock', 'noise' (sub/bot/presente: não conta) ou 'neutral'."""
    if not text:
        return "neutral"
    if NOISE_RE.search(text):
        return "noise"
    if LAUGH_RE.search(text):
        return "laugh"
    if SHOCK_RE.search(text):
        return "shock"
    return "neutral"


class _Ewma:
    """Média e variância móveis exponenciais."""

    def __init__(self, alpha: float, min_sd: float | None = None) -> None:
        self.alpha = alpha
        self.min_sd = min_sd  # None = regra do chat (proporcional à média)
        self.mean: float | None = None
        self.var = 0.0
        self.n = 0

    def z(self, x: float) -> float:
        if self.mean is None or self.n < 10:
            return 0.0
        if self.min_sd is not None:
            sd = max(math.sqrt(self.var), self.min_sd)
        else:
            sd = max(math.sqrt(self.var), 1.0 if self.mean >= 1 else 0.5, 0.35 * self.mean)
        return (x - self.mean) / sd

    def update(self, x: float) -> None:
        self.n += 1
        if self.mean is None:
            self.mean = x
            return
        d = x - self.mean
        self.mean += self.alpha * d
        self.var = (1 - self.alpha) * (self.var + self.alpha * d * d)


class HypeDetector:
    """Recebe chat e volume (1 valor por segundo); `check()` devolve o instante do momento quando dispara.
    `last_signals` explica o disparo (tipo, % de risada/susto no chat, força do grito) pra IA."""

    def __init__(self, cooldown_s: float = 120, z_threshold: float = 3.0, min_rate: float = 1.5,
                 alpha: float = 0.03) -> None:
        self.cooldown_s = cooldown_s
        self.z_threshold = z_threshold
        self.min_rate = min_rate
        self._chat = _Ewma(alpha)
        self._audio = _Ewma(0.02, min_sd=3.0)  # dB
        self._buckets: dict[int, list[int]] = {}  # idx -> [n, risada, susto]
        self._audio_z: deque[tuple[float, float]] = deque(maxlen=240)  # (ts, z) por segundo
        self._recent: deque[tuple[float, float, dict]] = deque(maxlen=16)  # (ts, score, sinais)
        self._closed_upto: int | None = None
        self._win: deque[tuple[int, int, int]] = deque(maxlen=3)  # últimos 3 baldes (6 s)
        self._last_fire = -1e18
        self.last_signals: dict = {}
        self._lock = threading.Lock()

    # ---------- entradas ----------
    def feed_chat(self, ts: float, text: str = "") -> None:
        kind = classify(text)
        if kind == "noise":
            return
        idx = int(ts // BUCKET_S)
        with self._lock:
            if self._closed_upto is not None and idx <= self._closed_upto:
                idx = self._closed_upto + 1
            b = self._buckets.setdefault(idx, [0, 0, 0])
            b[0] += 1
            b[1] += kind == "laugh"
            b[2] += kind == "shock"

    def feed_audio(self, ts: float, db: float) -> None:
        """Volume (dB) do segundo que terminou em `ts`."""
        with self._lock:
            z = self._audio.z(db)
            # grito não "puxa" a média pra cima
            self._audio.update(db if z < SCREAM_Z else self._audio.mean + 0.1 * (db - self._audio.mean))
            self._audio_z.append((ts, z))

    # ---------- decisão ----------
    def _scream_near(self, t: float) -> float:
        """Maior grito entre 10 s antes e 2 s depois (o chat reage atrasado)."""
        zs = [z for (ts, z) in self._audio_z if t - 10 <= ts <= t + 2]
        return max(zs, default=0.0)

    def _close_buckets(self, now: float) -> None:
        cur = int(now // BUCKET_S)
        if self._closed_upto is None:
            self._closed_upto = (min(self._buckets) - 1) if self._buckets else cur - 1
        for idx in range(self._closed_upto + 1, cur):
            self._win.append(tuple(self._buckets.pop(idx, [0, 0, 0])))
            n = sum(w[0] for w in self._win)
            laugh = sum(w[1] for w in self._win) / n if n else 0.0
            shock = sum(w[2] for w in self._win) / n if n else 0.0
            rate = n / (BUCKET_S * len(self._win))  # taxa sustentada em ~6 s
            z = self._chat.z(rate)
            self._chat.update(rate if z < self.z_threshold else self._chat.mean + 0.2 * (rate - self._chat.mean))
            t = (idx + 0.5) * BUCKET_S
            scream = self._scream_near(t)
            score = z
            if n >= 4 and laugh >= LAUGH_SHARE:
                score += 1.5
            if n >= 4 and shock >= SHOCK_SHARE:
                score += 1.0
            if scream >= SCREAM_Z:
                score += 0.6 * scream
            # só vira momento se houver REAÇÃO: risada, susto/choque ou grito
            emotional = (n >= 4 and (laugh >= LAUGH_SHARE or shock >= SHOCK_SHARE)) or scream >= SCREAM_Z
            if rate < self.min_rate or not emotional:
                score = min(score, self.z_threshold - 0.01)
            kind = ("risada" if laugh >= LAUGH_SHARE and laugh >= shock else
                    "susto/choque" if shock >= SHOCK_SHARE else "grito" if scream >= SCREAM_Z else "")
            signals = {"kind": kind, "laugh": round(laugh, 2), "shock": round(shock, 2),
                       "scream": round(scream, 1), "chat_z": round(z, 1), "rate": round(rate, 1)}
            self._recent.append((t, score, signals))
        self._closed_upto = max(self._closed_upto, cur - 1)

    def check(self, now: float | None = None) -> float | None:
        now = time.time() if now is None else now
        with self._lock:
            self._close_buckets(now)
            if now - self._last_fire < self.cooldown_s:
                return None
            recent = [r for r in self._recent if now - r[0] <= 10]
            if not recent:
                return None
            peak_ts, peak_score, signals = max(recent, key=lambda r: r[1])
            if peak_score < self.z_threshold:
                return None
            self._last_fire = now
            self.last_signals = signals
            # o momento real costuma ser ~3 s antes do chat explodir
            return peak_ts - 3.0

    def force(self, now: float | None = None) -> float:
        """Disparo manual (botão no painel)."""
        now = time.time() if now is None else now
        with self._lock:
            self._last_fire = now
            self.last_signals = {"kind": "manual"}
        return now


def describe(signals: dict | None) -> str:
    """Texto curto dos sinais pra IA (ex.: 'chat: 64% risada (KKKK); grito no áudio (+3.1)')."""
    if not signals:
        return ""
    if signals.get("kind") == "manual":
        return "corte pedido à mão (sem sinal automático)"
    parts = []
    if signals.get("laugh", 0) >= LAUGH_SHARE:
        parts.append(f"chat {round(100 * signals['laugh'])}% risada (KKKK/KEKW)")
    if signals.get("shock", 0) >= SHOCK_SHARE:
        parts.append(f"chat {round(100 * signals['shock'])}% susto/choque (WTF/???/socorro)")
    if signals.get("scream", 0) >= SCREAM_Z:
        parts.append(f"grito/pico de volume no áudio (+{signals['scream']} desvios)")
    if signals.get("chat_z"):
        parts.append(f"chat {signals['chat_z']}x acima do normal")
    return "; ".join(parts)
