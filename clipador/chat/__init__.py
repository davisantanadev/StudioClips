"""Leitores de chat ao vivo. Cada um chama `on_msg(ts, texto)` para cada mensagem."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from ..config import Channel

log = logging.getLogger(__name__)
OnMsg = Callable[[float, str], None]


class ChatReader:
    """Base: roda `_run()` numa thread e reconecta sozinho se cair."""

    def __init__(self, channel: Channel, on_msg: OnMsg) -> None:
        self.channel = channel
        self.on_msg = on_msg
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"chat-{self.channel.channel}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run()
            except Exception as e:
                log.warning("chat %s caiu (%s), reconectando em 15s", self.channel.url, e)
            self._stop.wait(15)

    def _run(self) -> None:  # pragma: no cover - implementado nas subclasses
        raise NotImplementedError


def make_reader(channel: Channel, on_msg: OnMsg) -> ChatReader:
    if channel.platform == "twitch":
        from .twitch import TwitchChat
        return TwitchChat(channel, on_msg)
    if channel.platform == "kick":
        from .kick import KickChat
        return KickChat(channel, on_msg)
    from .youtube import YouTubeChat
    return YouTubeChat(channel, on_msg)
