"""Acha sozinho as lives de GTA mais vistas agora (Twitch, Kick e YouTube) e reconhece o dono de uma VOD pelo link.

- Twitch: categoria "Grand Theft Auto VI" (API pública que o próprio site usa).
- Kick: subcategoria "grand-theft-auto-vi".
- YouTube: busca de lives "GTA 6" (yt-dlp), só as que estão ao vivo e falam de GTA no título.
Quem já está na lista (pelo canal ou pelo nome) não entra de novo. Nada aqui levanta erro pra fora:
uma plataforma fora do ar não atrapalha as outras.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass

from .config import Channel, Streamer
from .lang import guess_lang  # noqa: F401 (usado aqui e pelos testes)
from .recorder import GTA_RE, NO_WINDOW

log = logging.getLogger(__name__)
TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_PUBLIC_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # o mesmo do site da Twitch (o streamlink também usa)
TWITCH_GAME = "Grand Theft Auto VI"
KICK_SUBCATEGORY = "grand-theft-auto-vi"
YOUTUBE_LIVE_SEARCH = "https://www.youtube.com/results?search_query={q}&sp=EgJAAQ%3D%3D"  # filtro "ao vivo"
UTF8 = {"text": True, "encoding": "utf-8", "errors": "replace"}


@dataclass
class Found:
    platform: str
    channel: str      # login (Twitch), slug (Kick) ou ID do canal UC… (YouTube)
    display: str
    viewers: int
    lang: str = ""    # "" = não sei (o Whisper descobre sozinho)
    title: str = ""
    handle: str = ""  # YouTube: @handle, quando souber


# ---------- idioma ----------
_LANG_NAMES = {"portuguese": "pt", "english": "en", "spanish": "es", "german": "de", "russian": "ru",
               "french": "fr", "turkish": "tr", "italian": "it", "arabic": "ar"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold().lstrip("@"))


_SUFFIXES = ("oficial", "official", "live", "ao vivo", "aovivo", "tv", "gaming", "plays", "clips", "yt")


def _base(name: str) -> str:
    """Nome sem "Live", "Oficial", "TV"… no fim: "Kai Cenat Live" e "Kai Cenat" são a mesma pessoa."""
    n = _norm(name)
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            s = _norm(suf)
            if n.endswith(s) and len(n) - len(s) >= 4:
                n, changed = n[: -len(s)], True
    return n


def auto_name(platform: str, channel: str) -> str:
    return f"auto-{platform}-{re.sub(r'[^a-z0-9_-]', '', channel.lower())}"


# ---------- leitura das respostas ----------
def parse_twitch(j: dict) -> list[Found]:
    game = ((j or {}).get("data") or {}).get("game") or {}
    out = []
    for e in (game.get("streams") or {}).get("edges") or []:
        n = e.get("node") or {}
        b = n.get("broadcaster") or {}
        if not b.get("login"):
            continue
        lang = str(((b.get("broadcastSettings") or {}).get("language")) or "").lower()[:2]
        out.append(Found("twitch", b["login"], b.get("displayName") or b["login"], int(n.get("viewersCount") or 0),
                         lang if lang.isalpha() else guess_lang(n.get("title", "")), n.get("title") or ""))
    return out


def parse_kick(j: dict) -> list[Found]:
    out = []
    for s in (j or {}).get("data") or []:
        ch = s.get("channel") or {}
        slug = ch.get("slug")
        if not slug:
            continue
        title = s.get("session_title") or ""
        lang = _LANG_NAMES.get(str(s.get("language") or "").casefold(), "") or guess_lang(title)
        out.append(Found("kick", slug, (ch.get("user") or {}).get("username") or slug, int(s.get("viewer_count") or 0),
                         lang, title))
    return out


def parse_youtube(j: dict) -> list[Found]:
    best: dict[str, Found] = {}
    for e in (j or {}).get("entries") or []:
        cid, title = e.get("channel_id"), e.get("title") or ""
        if not cid or e.get("live_status") != "is_live" or not GTA_RE.search(title):
            continue
        f = Found("youtube", cid, e.get("channel") or cid, int(e.get("concurrent_view_count") or 0),
                  guess_lang(title), title)
        if cid not in best or f.viewers > best[cid].viewers:
            best[cid] = f
    return list(best.values())


# ---------- busca nas plataformas ----------
def _http_post(url, payload, headers):
    from curl_cffi import requests as creq
    r = creq.post(url, json=payload, headers=headers, impersonate="chrome", timeout=25)
    r.raise_for_status()
    return r.json()


def _http_get(url):
    from curl_cffi import requests as creq
    r = creq.get(url, impersonate="chrome", timeout=25)
    r.raise_for_status()
    return r.json()


def _yt_dlp_json(args: list[str], timeout: int = 90) -> dict:
    p = subprocess.run([sys.executable, "-m", "yt_dlp", *args], capture_output=True, timeout=timeout,
                       creationflags=NO_WINDOW, **UTF8)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def twitch_top(limit: int = 30) -> list[Found]:
    q = {"query": 'query{game(name:"' + TWITCH_GAME + '"){streams(first:' + str(int(limit)) + '){edges{node{'
                  'viewersCount title broadcaster{login displayName broadcastSettings{language}}}}}}}'}
    return parse_twitch(_http_post(TWITCH_GQL, q, {"Client-Id": TWITCH_PUBLIC_CLIENT}))


def kick_top(limit: int = 30) -> list[Found]:
    return parse_kick(_http_get(f"https://kick.com/stream/livestreams/pt?page=1&limit={limit}"
                                f"&subcategory={KICK_SUBCATEGORY}&sort=desc"))


def youtube_top(limit: int = 20) -> list[Found]:
    found: dict[str, Found] = {}
    for q in ("GTA+6", "GTA+VI"):
        for f in parse_youtube(_yt_dlp_json(["--flat-playlist", "--playlist-end", str(limit), "-J",
                                             YOUTUBE_LIVE_SEARCH.format(q=q)])):
            if f.channel not in found or f.viewers > found[f.channel].viewers:
                found[f.channel] = f
    return list(found.values())


def discover_all() -> list[Found]:
    out: list[Found] = []
    for name, fn in (("Twitch", twitch_top), ("Kick", kick_top), ("YouTube", youtube_top)):
        try:
            out += fn()
        except Exception as e:
            log.info("procura de lives no %s falhou (%s) — sigo com as outras", name, e)
    return out


# ---------- juntar com a lista ----------
def _fixed_keys(fixed: list[Streamer]) -> tuple[set[tuple[str, str]], set[str]]:
    chans = {(c.platform, c.channel.lower()) for s in fixed for c in s.channels}
    names = {_base(s.display) for s in fixed} | {_base(s.name) for s in fixed}
    return chans, names


def to_streamer(f: Found, auto: bool = False) -> Streamer:
    base = f.channel if f.platform != "youtube" else (f.handle or f.display or f.channel)
    name = auto_name(f.platform, f.channel) if auto else (_norm(base) or auto_name(f.platform, f.channel))
    return Streamer(name, f.display or f.channel, f.lang or guess_lang(f.title),
                    [Channel(f.platform, f.channel)], enabled=True, auto=auto)


def merge(fixed: list[Streamer], found: list[Found], min_viewers: int, max_n: int,
          disabled: set[str] | None = None, langs: set[str] | None = None) -> list[Streamer]:
    """Os achados que valem gravar: fora da lista, com audiência, um por pessoa, os mais vistos primeiro.
    `langs`: só esses idiomas (idioma desconhecido entra: o Whisper descobre); None = todos."""
    chans, names = _fixed_keys(fixed)
    disabled = disabled or set()
    out: list[Streamer] = []
    for f in sorted(found, key=lambda x: -x.viewers):
        if len(out) >= max_n:
            break
        if f.viewers < min_viewers or (f.platform, f.channel.lower()) in chans or _base(f.display) in names:
            continue
        if langs and f.lang not in langs:  # idioma desconhecido também fica de fora
            continue
        if auto_name(f.platform, f.channel) in disabled:
            continue
        names.add(_base(f.display))
        out.append(to_streamer(f, auto=True))
    return out


# ---------- VOD: de quem é o link ----------
def vod_ref(url: str) -> tuple[str, str] | None:
    u = url or ""
    if m := re.search(r"twitch\.tv/videos/(\d+)", u):
        return "twitch", m.group(1)
    if m := re.search(r"kick\.com/([^/?#]+)/videos/", u):
        return "kick", m.group(1)
    if m := re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|shorts/)|youtu\.be/)([\w-]{6,})", u):
        return "youtube", m.group(1)
    return None


def identify_vod(url: str) -> Found | None:
    """Dono da VOD (canal, nome e idioma). None se o link não for de Twitch/Kick/YouTube ou não deu pra ler."""
    ref = vod_ref(url)
    if not ref:
        return None
    platform, key = ref
    try:
        if platform == "twitch":
            j = _http_post(TWITCH_GQL, {"query": 'query{video(id:"' + key + '"){title owner{login displayName '
                                                 'broadcastSettings{language}}}}'},
                           {"Client-Id": TWITCH_PUBLIC_CLIENT})
            v = ((j.get("data") or {}).get("video")) or {}
            o = v.get("owner") or {}
            if not o.get("login"):
                return None
            lang = str((o.get("broadcastSettings") or {}).get("language") or "").lower()[:2]
            return Found("twitch", o["login"], o.get("displayName") or o["login"], 0,
                         lang if lang.isalpha() else guess_lang(v.get("title", "")), v.get("title") or "")
        if platform == "kick":
            try:
                ch = _http_get(f"https://kick.com/api/v2/channels/{key}")
                display = (ch.get("user") or {}).get("username") or key
            except Exception:
                display = key
            return Found("kick", key, display, 0)
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--print",
                            "%(channel_id)s\t%(channel)s\t%(uploader_id)s\t%(language)s\t%(title)s", url],
                           capture_output=True, timeout=60, creationflags=NO_WINDOW, **UTF8)
        parts = (p.stdout.strip().splitlines() or [""])[-1].split("\t")
        if len(parts) < 5 or not parts[0].startswith("UC"):
            return None
        cid, name, handle, lang, title = parts[:5]
        lang = "" if lang in ("NA", "None") else lang[:2]
        return Found("youtube", cid, name, 0, lang or guess_lang(title), title,
                     handle=handle if handle.startswith("@") else "")
    except Exception as e:
        log.info("não deu pra reconhecer o dono da VOD (%s)", e)
        return None


def match_streamer(f: Found, streamers: list[Streamer]) -> Streamer | None:
    keys = {f.channel.lower()} | ({f.handle.lower()} if f.handle else set())
    for s in streamers:
        if any(c.platform == f.platform and c.channel.lower() in keys for c in s.channels):
            return s
    want = {_norm(f.display)} | ({_norm(f.handle)} if f.handle else set())
    want.discard("")
    for s in streamers:
        if want & {_norm(s.display), _norm(s.name)}:
            return s
    return None
