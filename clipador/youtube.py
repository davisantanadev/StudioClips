"""Postar clipes no YouTube pelo Composio (a conta do YouTube fica conectada no Composio, não no Clipador).

- Postar agora: sobe o vídeo como público.
- Agendar: sobe como privado e marca o horário no próprio YouTube (publishAt): ele publica sozinho na hora,
  mesmo com o PC desligado. Nada é enviado sem você clicar.

Usa o composio.exe instalado no PC (comando `composio login` uma vez + conectar o YouTube pelo painel).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
CATEGORY_GAMING = "20"
UPLOAD_TOOL = "YOUTUBE_MULTIPART_UPLOAD_VIDEO"
VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")


class ComposioError(RuntimeError):
    pass


def composio_bin() -> str | None:
    found = shutil.which("composio")
    if found:
        return found
    p = Path.home() / ".composio" / ("composio.exe" if sys.platform == "win32" else "composio")
    return str(p) if p.exists() else None


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    exe = composio_bin()
    if not exe:
        raise ComposioError("Composio não está instalado neste PC")
    env = {**os.environ, "NO_COLOR": "1"}
    return subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW, env=env)


def _json(out: str):
    """A saída do composio às vezes vem com aviso antes do JSON: pega o primeiro objeto/lista."""
    out = (out or "").strip()
    for start in (out.find("{"), out.find("[")):
        if start >= 0:
            try:
                return json.loads(out[start:])
            except ValueError:
                continue
    return None


# ---------- textos dentro dos limites do YouTube ----------
def clean_title(title: str) -> str:
    t = re.sub(r"[<>]", "", re.sub(r"\s+", " ", title or "")).strip()
    return t[:100].rstrip() or "Clipe de GTA 6"


def clean_tags(hashtags: list[str], limit: int = 450) -> list[str]:
    out, total = [], 0
    for h in hashtags:
        tag = re.sub(r"[<>,]", "", str(h).lstrip("#")).strip()
        if not tag or tag.lower() in (x.lower() for x in out):
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)
        if total + cost > limit:
            break
        out.append(tag)
        total += cost
    return out


def build_description(description: str, hashtags: list[str], max_bytes: int = 4900) -> str:
    tags = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags if h)
    text = (description or "").strip()
    while len((text + "\n\n" + tags).encode("utf-8")) > max_bytes and text:
        text = text[:-50].rstrip()
    return (text + ("\n\n" + tags if tags else "")).strip()


def find_video_id(obj) -> str | None:
    """O ID do vídeo (11 caracteres) em qualquer canto da resposta."""
    if isinstance(obj, dict):
        for k in ("id", "videoId", "video_id"):
            v = obj.get(k)
            if isinstance(v, str) and VIDEO_ID_RE.match(v):
                return v
        for v in obj.values():
            got = find_video_id(v)
            if got:
                return got
    if isinstance(obj, list):
        for v in obj:
            got = find_video_id(v)
            if got:
                return got
    return None


# ---------- conta ----------
def status() -> dict:
    if not composio_bin():
        return {"installed": False, "logged_in": False, "connected": False}
    try:
        who = _run(["whoami"], 30)
        logged = who.returncode == 0 and bool(_json(who.stdout))
        conns = _json(_run(["connections", "list"], 30).stdout) if logged else None
    except (subprocess.TimeoutExpired, ComposioError):
        return {"installed": True, "logged_in": False, "connected": False}
    yt_accounts = (conns or {}).get("youtube", []) if isinstance(conns, dict) else []
    connected = any(str(a.get("status", "")).upper() == "ACTIVE" for a in yt_accounts if isinstance(a, dict))
    return {"installed": True, "logged_in": logged, "connected": connected}


def connect_url() -> str:
    j = _json(_run(["link", "youtube", "--no-wait"], 60).stdout) or {}
    url = j.get("redirect_url") if isinstance(j, dict) else None
    if not url:
        raise ComposioError("o Composio não devolveu o link pra conectar o YouTube")
    return url


# ---------- postar ----------
def _with_body(args: list[str], body: dict, timeout: int):
    fd, path = tempfile.mkstemp(suffix=".json", prefix="clipador_yt_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        return _run([*args, "-d", f"@{path}"], timeout)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def upload(video: Path, title: str, description: str, tags: list[str], privacy: str) -> str:
    """Sobe o vídeo e devolve o ID dele no YouTube."""
    body = {"title": clean_title(title), "description": description, "tags": tags,
            "categoryId": CATEGORY_GAMING, "privacyStatus": privacy}
    r = _with_body(["execute", UPLOAD_TOOL, "--file", str(Path(video).resolve())], body, timeout=1800)
    j = _json(r.stdout) or {}
    if r.returncode != 0 or (isinstance(j, dict) and j.get("successful") is False):
        err = (j.get("error") if isinstance(j, dict) else None) or r.stderr.strip()[-300:] or r.stdout.strip()[-300:]
        raise ComposioError(f"o YouTube recusou o envio: {err}")
    vid = find_video_id(j)
    if not vid:
        raise ComposioError("o vídeo subiu, mas não veio o ID dele na resposta")
    return vid


def schedule(video_id: str, when_utc: datetime) -> None:
    """Marca o horário de publicação no próprio YouTube (o vídeo fica privado até lá)."""
    iso = when_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {"id": video_id, "status": {"privacyStatus": "private", "publishAt": iso,
                                       "selfDeclaredMadeForKids": False}}
    r = _with_body(["proxy", "https://www.googleapis.com/youtube/v3/videos?part=status", "--toolkit", "youtube",
                    "-X", "PUT", "-H", "content-type: application/json"], body, timeout=120)
    j = _json(r.stdout)
    bad = r.returncode != 0 or (isinstance(j, dict) and (j.get("error") or j.get("successful") is False))
    if bad:
        raise ComposioError(f"o vídeo subiu privado, mas o YouTube não aceitou o horário: "
                            f"{(r.stdout or r.stderr).strip()[-300:]}")
