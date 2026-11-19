"""Chat da Kick via websocket público (Pusher)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

from . import ChatReader

# A Kick troca a chave do chat de tempos em tempos (a 32cbd… foi aposentada em out/2026). O site pergunta a
# chave atual em REALTIME_URL antes de conectar; fazemos igual e só usamos a reserva se essa consulta falhar.
REALTIME_URL = "https://web.kick.com/api/v1/realtime/connection"
FALLBACK_KEY = "34bf7a0ff419a2a775b9"
FALLBACK_CLUSTER = "us2"
log = logging.getLogger(__name__)


def _post_json(url: str, body: dict) -> dict:
    from curl_cffi import requests as creq
    r = creq.post(url, json=body, impersonate="chrome", timeout=20)
    r.raise_for_status()
    return r.json()


def parse_connection(resp: dict) -> tuple[str, str] | None:
    """(chave, cluster) do Pusher na resposta da Kick, ou None."""
    for c in ((resp or {}).get("data") or {}).get("connections") or []:
        cred = c.get("credentials") or {}
        if c.get("provider") == "pusher" and cred.get("app_key"):
            return str(cred["app_key"]), str(cred.get("cluster") or FALLBACK_CLUSTER)
    return None


def realtime_credentials() -> tuple[str, str]:
    forced = os.environ.get("KICK_PUSHER_KEY")
    if forced:
        return forced, os.environ.get("KICK_PUSHER_CLUSTER", FALLBACK_CLUSTER)
    body = {"client": {"id": str(uuid.uuid4()), "type": "web"},
            "capabilities": {"accepted_providers": [{"provider": "pusher"}]}}
    try:
        got = parse_connection(_post_json(REALTIME_URL, body))
        if got:
            return got
    except Exception as e:
        log.debug("chave do chat da Kick: consulta falhou (%s), usando a reserva", e)
    return FALLBACK_KEY, FALLBACK_CLUSTER


def ws_url(key: str, cluster: str) -> str:
    return f"wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0&flash=false"


def chatroom_id(slug: str) -> int:
    """A API da Kick fica atrás do Cloudflare; curl_cffi imita um navegador."""
    url = f"https://kick.com/api/v2/channels/{slug}"
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, impersonate="chrome", timeout=20)
    except ImportError:
        import httpx
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    return int(r.json()["chatroom"]["id"])


def parse_event(raw: str) -> str | None:
    msg = json.loads(raw)
    if not str(msg.get("event", "")).endswith("ChatMessageEvent"):
        return None
    data = msg.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    return str((data or {}).get("content", "")).strip() or None


class KickChat(ChatReader):
    def _run(self) -> None:
        asyncio.run(self._arun())

    async def _arun(self) -> None:
        import websockets

        room = await asyncio.to_thread(chatroom_id, self.channel.channel)
        key, cluster = await asyncio.to_thread(realtime_credentials)  # a cada reconexão: pega chave nova
        async with websockets.connect(ws_url(key, cluster), ping_interval=60) as ws:
            sub = {"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}
            await ws.send(json.dumps(sub))
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if '"pusher:ping"' in raw:
                    await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                    continue
                text = parse_event(raw)
                if text:
                    self.on_msg(time.time(), text)
