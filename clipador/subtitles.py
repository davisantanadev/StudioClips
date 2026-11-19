"""Legendas ASS: karaokê palavra por palavra (PT) ou frase por frase (tradução)."""
from __future__ import annotations

import textwrap
from typing import Literal

from .ai import Transcript, Word
from .texts import strip_emoji

W, H = 1080, 1920


def ass_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(t, 0.0)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(s: str) -> str:
    return strip_emoji(s).replace("{", "(").replace("}", ")").replace("\\", "").strip()


def _header(font: str, accent: str) -> str:
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font},104,&H00FFFFFF,{ass_color(accent)},&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,7,3,5,40,40,0,1
Style: Phrase,{font},82,&H00FFFFFF,{ass_color(accent)},&H00000000,&H96000000,0,0,0,0,100,100,0.5,0,1,6,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _chunks(words: list[Word], max_words: int = 3, max_chars: int = 16, gap: float = 0.6) -> list[list[Word]]:
    out: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if not any(c.isalnum() for c in _clean(w.text)):  # "...", "-": não vira legenda
            continue
        too_long = sum(len(x.text) + 1 for x in cur) + len(w.text) > max_chars
        if cur and (len(cur) >= max_words or too_long or w.start - cur[-1].end > gap):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def karaoke_events(t: Transcript, accent: str, y: int) -> list[str]:
    words = [w for s in t.segments for w in s.words]
    chunks = _chunks(words)
    ev = []
    acc = ass_color(accent)
    for ci, ch in enumerate(chunks):
        next_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else ch[-1].end + 0.6
        chunk_end = min(ch[-1].end + 0.35, next_start)
        for wi, w in enumerate(ch):
            st = w.start
            en = ch[wi + 1].start if wi + 1 < len(ch) else chunk_end
            if en <= st:
                en = st + 0.08
            parts = []
            for k, x in enumerate(ch):
                txt = _clean(x.text).upper()
                if k == wi:
                    parts.append(f"{{\\c{acc}\\fscx112\\fscy112}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            pop = "{\\fad(0,0)\\t(0,80,\\fscx104\\fscy104)\\t(80,160,\\fscx100\\fscy100)}" if wi == 0 else ""
            ev.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Karaoke,,0,0,0,,{{\\an5\\pos({W // 2},{y})}}{pop}"
                      + " ".join(parts))
    return ev


def phrase_events(t: Transcript, y: int, width: int = 26) -> list[str]:
    ev = []
    for s in t.segments:
        lines = textwrap.wrap(_clean(s.text), width)
        if not lines:
            continue
        pages = [lines[i:i + 2] for i in range(0, len(lines), 2)]
        total = sum(len(" ".join(p)) for p in pages) or 1
        t0 = s.start
        for p in pages:
            dur = (s.end - s.start) * len(" ".join(p)) / total
            ev.append(f"Dialogue: 0,{_ts(t0)},{_ts(t0 + dur)},Phrase,,0,0,0,,"
                      f"{{\\an5\\pos({W // 2},{y})\\fad(80,60)}}" + "\\N".join(p))
            t0 += dur
    return ev


def build_ass(t: Transcript, mode: Literal["karaoke", "phrase"], accent: str = "#FF2E63",
              font: str = "Anton", y: int = 1480) -> str:
    has_words = any(s.words for s in t.segments)
    if mode == "karaoke" and has_words:
        events = karaoke_events(t, accent, y)
    else:
        events = phrase_events(t, y)
    return _header(font, accent) + "\n".join(events) + "\n"
