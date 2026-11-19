"""Chat da Twitch via IRC anônimo (sem login)."""
from __future__ import annotations

import random
import socket
import time

from . import ChatReader


def parse_privmsg(line: str) -> str | None:
    """Extrai o texto de uma linha IRC PRIVMSG (com ou sem tags)."""
    if " PRIVMSG #" not in line:
        return None
    rest = line.split(" PRIVMSG #", 1)[1]
    if " :" not in rest:
        return None
    return rest.split(" :", 1)[1].strip()


class TwitchChat(ChatReader):
    def _run(self) -> None:
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            s.sendall(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{self.channel.channel.lower()}\r\n".encode())
            buf = b""
            s.settimeout(1.0)
            last = time.time()
            while not self._stop.is_set():
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    if time.time() - last > 360:
                        raise ConnectionError("sem dados há 6 min")
                    continue
                if not data:
                    raise ConnectionError("conexão fechada")
                last = time.time()
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "ignore")
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                        continue
                    text = parse_privmsg(line)
                    if text is not None:
                        self.on_msg(time.time(), text)
        finally:
            s.close()
