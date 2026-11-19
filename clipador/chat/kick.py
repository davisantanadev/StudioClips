"""Chat da Kick via websocket público (Pusher)."""
from __future__ import annotations

import asyncio
import json
import os
import time

from . import ChatReader

PUSHER_KEY = os.environ.get("KICK_PUSHER_KEY", "32cbd69e4b950bf97679")
WS_URL = f"wss://ws-us2.pusher.com/app/{PUSHER_KEY}?protocol=7&client=js&version=8.4.0&flash=false"


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
        async with websockets.connect(WS_URL, ping_interval=60) as ws:
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
