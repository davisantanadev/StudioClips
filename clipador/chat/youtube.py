"""Chat ao vivo do YouTube via chat-downloader."""
from __future__ import annotations

import time

from . import ChatReader


class YouTubeChat(ChatReader):
    def _run(self) -> None:
        from chat_downloader import ChatDownloader

        chat = ChatDownloader().get_chat(self.channel.url, message_types=["text_message"])
        for m in chat:
            if self._stop.is_set():
                break
            text = str(m.get("message", "")).strip()
            if text:
                self.on_msg(time.time(), text)
