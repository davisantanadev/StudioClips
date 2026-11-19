"""Clipes da Twitch como sinal de momento: quando os espectadores clipam um trecho, ele foi bom.

- VOD: busca os clipes daquela VOD (com `vod_offset`) e dá mais peso aos trechos clipados.
- Ao vivo: a cada ~45 s olha os clipes recém-criados; 2 ou mais em ~2 min disparam um corte.

Usa a API oficial (Helix) com o login de app (client credentials). Precisa de um Client ID e um Client
Secret grátis em https://dev.twitch.tv/console/apps — ficam no .env da pasta do usuário, nunca no repositório.
Sem eles, nada muda: o Clipador segue só com chat e áudio.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)
API = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
SIGNUP = "https://dev.twitch.tv/console/apps"
NEAR_S = 15          # clipes a até 15 s um do outro contam como o mesmo momento
LIVE_WINDOW_S = 120  # ao vivo: clipes criados nos últimos 2 min
LIVE_MIN_CLIPS = 2


@dataclass
class ClipHit:
    t: float      # VOD: segundo do momento na VOD
    views: int


def to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str) -> float:
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def parse_duration(s: str) -> float:
    """'3h2m10s' -> segundos."""
    total = 0.0
    for n, unit in re.findall(r"(\d+)([hms])", s or ""):
        total += int(n) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def video_id_from_url(url: str) -> str | None:
    m = re.search(r"twitch\.tv/videos/(\d+)", url or "")
    return m.group(1) if m else None


def vod_hits(clips: list[dict], video_id: str) -> list[ClipHit]:
    """Clipes desta VOD. O momento fica perto do fim do clipe (quem clipa aperta logo depois de ver)."""
    out = []
    for c in clips:
        off = c.get("vod_offset")
        if str(c.get("video_id")) != str(video_id) or off is None:
            continue
        dur = float(c.get("duration") or 30)
        out.append(ClipHit(float(off) + min(dur * 0.7, max(dur - 3, 0)), int(c.get("view_count") or 0)))
    return out


def _bonus(count: int, views: int) -> float:
    return min(4.5, 1.6 * count) + min(1.5, math.log10(1 + views) / 2)


def hits_near(hits: list[ClipHit], t: float, radius: float = 30) -> tuple[int, int]:
    near = [h for h in hits if abs(h.t - t) <= radius]
    return len(near), sum(h.views for h in near)


def boost(scores: list[float], hits: list[ClipHit], bucket: float) -> list[float]:
    """Soma pontos nos trechos clipados (mais clipes e mais views = mais pontos). Não altera `scores`."""
    out = list(scores)
    touched: set[int] = set()
    span = int(math.ceil(NEAR_S / bucket))
    for h in hits:
        c = int(h.t // bucket)
        touched.update(i for i in range(c - span, c + span + 1) if 0 <= i < len(out))
    for i in touched:
        n, v = hits_near(hits, (i + 0.5) * bucket, NEAR_S)
        if n:
            out[i] += _bonus(n, v)
    return out


def live_burst(clips: list[dict], seen: set[str], now: float, window: float = LIVE_WINDOW_S,
               min_count: int = LIVE_MIN_CLIPS) -> tuple[float, int, int] | None:
    """(instante do momento, nº de clipes, views) quando várias pessoas clipam de uma vez; None se não."""
    fresh = [c for c in clips if c.get("id") not in seen and now - from_iso(c["created_at"]) <= window]
    if len(fresh) < min_count:
        return None
    seen.update(c["id"] for c in fresh)
    first = min(from_iso(c["created_at"]) for c in fresh)
    return first - 12.0, len(fresh), sum(int(c.get("view_count") or 0) for c in fresh)


class _Http:
    def post(self, url, data=None, timeout=20):
        import httpx
        r = httpx.post(url, data=data, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def get(self, url, params=None, headers=None, timeout=20):
        import httpx
        r = httpx.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()


class Helix:
    def __init__(self, client_id: str, secret: str, http=None) -> None:
        self.client_id = client_id
        self.secret = secret
        self.http = http or _Http()
        self._token: str | None = None
        self._token_until = 0.0
        self._users: dict[str, str] = {}
        self._lock = threading.Lock()

    def _auth(self) -> str:
        with self._lock:
            if not self._token or time.time() > self._token_until:
                j = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.secret,
                                                    "grant_type": "client_credentials"})
                self._token = j["access_token"]
                self._token_until = time.time() + float(j.get("expires_in", 3600)) - 120
            return self._token

    def get(self, path: str, params: dict) -> dict:
        for attempt in (0, 1):
            try:
                return self.http.get(f"{API}{path}", params=params,
                                     headers={"Client-Id": self.client_id, "Authorization": f"Bearer {self._auth()}"})
            except Exception as e:
                if attempt == 0 and getattr(getattr(e, "response", None), "status_code", None) == 401:
                    self._token = None  # token venceu: pega outro e tenta de novo
                    continue
                raise
        raise RuntimeError("inalcançável")

    def user_id(self, login: str) -> str | None:
        login = login.lower()
        if login not in self._users:
            data = self.get("/users", {"login": login}).get("data") or []
            if not data:
                return None
            self._users[login] = str(data[0]["id"])
        return self._users[login]

    def clips(self, broadcaster_id: str, started: float, ended: float, max_pages: int = 10) -> list[dict]:
        out, cursor = [], None
        for _ in range(max_pages):
            params = {"broadcaster_id": broadcaster_id, "started_at": to_iso(started), "ended_at": to_iso(ended),
                      "first": 100}
            if cursor:
                params["after"] = cursor
            j = self.get("/clips", params)
            out += j.get("data") or []
            cursor = (j.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return out

    def vod_clip_hits(self, video_id: str) -> list[ClipHit]:
        data = self.get("/videos", {"id": video_id}).get("data") or []
        if not data:
            return []
        v = data[0]
        start = from_iso(v["created_at"])
        end = start + parse_duration(v.get("duration", "")) + 7 * 86400  # tem quem clipe a VOD dias depois
        return vod_hits(self.clips(str(v["user_id"]), start, min(end, time.time())), video_id)

    def recent_clips(self, login: str, since_s: float = 240) -> list[dict]:
        uid = self.user_id(login)
        if not uid:
            return []
        now = time.time()
        return self.clips(uid, now - since_s, now, max_pages=1)


def has_credentials() -> bool:
    return bool(os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET"))


_helix: Helix | None = None


def helix() -> Helix | None:
    global _helix
    if not has_credentials():
        return None
    cid, sec = os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"]
    if _helix is None or (_helix.client_id, _helix.secret) != (cid, sec):
        _helix = Helix(cid, sec)
    return _helix


def vod_boost(url: str, scores: list[float], bucket: float, note=lambda m: None) -> tuple[list[float], list[ClipHit]]:
    """Para VOD da Twitch com credenciais: soma os clipes nos scores. Sem credencial ou se falhar, não muda nada."""
    vid, h = video_id_from_url(url), helix()
    if not vid or h is None:
        return scores, []
    try:
        hits = h.vod_clip_hits(vid)
    except Exception as e:
        note(f"clipes da Twitch indisponíveis ({e}) — sigo só com o chat/áudio")
        return scores, []
    if hits:
        note(f"🎬 {len(hits)} clipes que os espectadores fizeram nessa VOD entram como sinal")
    return boost(scores, hits, bucket), hits
