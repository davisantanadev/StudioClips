"""Chat ao vivo do YouTube pelo yt-dlp.

O chat-downloader parou de funcionar com o YouTube em nov/2026 ("Unable to parse initial video data"): as lives
do YouTube ficavam sem chat e nunca disparavam corte. O yt-dlp (que se mantém atualizado) grava o chat ao vivo
em pedaços de arquivo; aqui lemos esses pedaços conforme chegam, sem repetir mensagem.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

from . import ChatReader

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
UTF8 = {"text": True, "encoding": "utf-8", "errors": "replace"}


def live_video_id(url: str) -> str | None:
    """ID do vídeo que está ao vivo agora no canal (link .../live). None se não estiver ao vivo."""
    p = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--print", "%(id)s\t%(live_status)s",
                        url], capture_output=True, timeout=60, creationflags=NO_WINDOW, **UTF8)
    line = (p.stdout.strip().splitlines() or [""])[-1].split("\t")
    return line[0] if len(line) == 2 and line[1] == "is_live" and line[0] else None


def _run_text(run: dict) -> str:
    if "text" in run:
        return str(run["text"])
    emoji = run.get("emoji") or {}
    eid = str(emoji.get("emojiId") or "")
    if eid and len(eid) <= 4:  # emoji de verdade (😂); os personalizados do canal têm id comprido
        return eid
    shortcuts = emoji.get("shortcuts") or []
    return f" {shortcuts[0]} " if shortcuts else ""


def parse_line(line: str) -> list[tuple[str, float, str]]:
    """(id, horário, texto) das mensagens de uma linha do chat gravado pelo yt-dlp."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    actions = (obj.get("replayChatItemAction") or {}).get("actions") or [obj]
    out = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        item = ((a.get("addChatItemAction") or {}).get("item")) or {}
        r = item.get("liveChatTextMessageRenderer")
        if not r:
            continue
        text = "".join(_run_text(x) for x in (r.get("message") or {}).get("runs") or []).strip()
        try:
            ts = int(r.get("timestampUsec")) / 1e6
        except (TypeError, ValueError):
            continue
        if text:
            out.append((str(r.get("id") or f"{ts}:{text}"), ts, text))
    return out


def read_shared(path: Path) -> bytes:
    """Lê o arquivo sem prender ele: no Windows, abrir normal impede o yt-dlp de renomear/apagar o pedaço
    (e ele desiste com "O arquivo já está sendo usado por outro processo")."""
    if sys.platform != "win32":
        return Path(path).read_bytes()
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    GENERIC_READ, SHARE_ALL, OPEN_EXISTING = 0x80000000, 0x7, 3
    h = k32.CreateFileW(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, 0, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "não abriu", str(path))
    fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
    with os.fdopen(fd, "rb") as f:
        return f.read()


class FragmentTail:
    """Lê o arquivo de chat que o yt-dlp vai aumentando (…live_chat.json.part, que vira …live_chat.json no fim):
    uma mensagem por linha. Os pedaços …-FragN são a resposta crua do YouTube e ficam de fora.
    Lê sem prender o arquivo e filtra mensagem repetida pelo id."""

    def __init__(self, folder: Path, remember: int = 5000) -> None:
        self.folder = Path(folder)
        self._offset = 0
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._remember = remember

    def _file(self) -> Path | None:
        for name in ("chat.live_chat.json.part", "chat.live_chat.json"):
            p = self.folder / name
            if p.exists():
                return p
        hits = [p for p in self.folder.glob("*.live_chat.json*") if "Frag" not in p.name]
        return hits[0] if hits else None

    def poll(self) -> list[tuple[float, str]]:
        f = self._file()
        if f is None:
            return []
        try:
            data = read_shared(f)
        except OSError:
            return []
        if len(data) < self._offset:  # arquivo recomeçou: lê de novo (repetidas saem pelo id)
            self._offset = 0
        chunk = data[self._offset:]
        end = chunk.rfind(b"\n")
        if end < 0:
            return []
        self._offset += end + 1
        out = []
        for raw in chunk[:end].splitlines():
            for mid, ts, text in parse_line(raw.decode("utf-8", "replace")):
                if mid in self._seen:
                    continue
                self._seen[mid] = None
                if len(self._seen) > self._remember:
                    self._seen.popitem(last=False)
                out.append((ts, text))
        return out

def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=NO_WINDOW)
    else:
        proc.kill()


class YouTubeChat(ChatReader):
    _proc: subprocess.Popen | None = None

    def stop(self) -> None:
        super().stop()
        if self._proc is not None:  # desmarcou/fechou: o yt-dlp do chat sai junto, na hora
            _kill(self._proc)

    def _run(self) -> None:
        vid = live_video_id(self.channel.url)
        if not vid:
            raise ConnectionError("o canal não está ao vivo no YouTube agora")
        folder = Path(tempfile.mkdtemp(prefix="clipador_ytchat_"))
        proc = subprocess.Popen([sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs",
                                 "--sub-langs", "live_chat", "-o", str(folder / "chat"),
                                 f"https://www.youtube.com/watch?v={vid}"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self._proc = proc
        tail = FragmentTail(folder)
        try:
            while not self._stop.is_set():
                for ts, text in tail.poll():
                    self.on_msg(ts, text)
                if proc.poll() is not None:
                    raise ConnectionError("o chat do YouTube parou (a live acabou?)")
                self._stop.wait(1.0)
        finally:
            _kill(proc)
            shutil.rmtree(folder, ignore_errors=True)
