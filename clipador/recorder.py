"""Gravação contínua da live em segmentos de 10 s (buffer rolante) + checagem de quem está ao vivo."""
from __future__ import annotations

import csv
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Channel

log = logging.getLogger(__name__)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
GTA_RE = re.compile(r"gta\s*(6|vi)\b|grand theft auto\s*(6|vi)\b|gta", re.I)


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


@dataclass
class Segment:
    path: Path
    start_ts: float  # horário (relógio) do início do trecho
    dur: float

    @property
    def end_ts(self) -> float:
        return self.start_ts + self.dur


@dataclass
class LiveInfo:
    channel: Channel
    title: str
    category: str

    @property
    def is_gta(self) -> bool:
        return bool(GTA_RE.search(f"{self.title} {self.category}"))


def probe(channel: Channel) -> LiveInfo | None:
    """Devolve LiveInfo se o canal estiver ao vivo, senão None. Nunca levanta exceção."""
    try:
        from streamlink import Streamlink

        session = Streamlink()
        _, pcls, url = session.resolve_url(channel.url)
        plugin = pcls(session, url)
        streams = plugin.streams()
        if not streams:
            return None
        return LiveInfo(channel, plugin.get_title() or "", plugin.get_category() or "")
    except Exception as e:  # offline, canal inexistente, rede…
        log.debug("probe %s: %s", channel.url, e)
        return None


class Recorder:
    """streamlink → ffmpeg (segment, -c copy). Mantém os últimos `keep` segmentos."""

    def __init__(self, url: str, out_dir: Path, quality: str = "720p,720p60,480p,best",
                 segment_s: int = 10, keep: int = 18, source_cmd: list[str] | None = None,
                 on_segment: Callable[[Segment], None] | None = None) -> None:
        self.url = url
        self.out_dir = Path(out_dir)
        self.quality = quality
        self.segment_s = segment_s
        self.keep = keep
        self.source_cmd = source_cmd
        self.on_segment = on_segment
        self._src: subprocess.Popen | None = None
        self._ff: subprocess.Popen | None = None
        self._segs: list[Segment] = []
        self._trash: list[Path] = []  # no Windows o ffmpeg pode segurar o arquivo; tenta de novo depois
        self._seen: set[str] = set()
        self._offset: float | None = None
        self._last_new = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- ciclo de vida ----------
    def start(self) -> None:
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir, ignore_errors=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        src = self.source_cmd or [sys.executable, "-m", "streamlink", "--stdout",
                                  "--twitch-disable-ads", "--hls-live-edge", "2",
                                  self.url, self.quality]
        self._src = subprocess.Popen(src, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW)
        ff = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
              "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
              "-f", "segment", "-segment_time", str(self.segment_s),
              "-segment_list", "list.csv", "-segment_list_type", "csv",
              "-segment_list_flags", "+live", "-reset_timestamps", "1", "seg_%06d.ts"]
        self._ff = subprocess.Popen(ff, stdin=self._src.stdout, cwd=self.out_dir,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=NO_WINDOW)
        self._src.stdout.close()  # ffmpeg é o dono do pipe agora
        self._last_new = time.time()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for p in (self._src, self._ff):
            if p and p.poll() is None:
                p.terminate()
        for p in (self._src, self._ff):
            if p:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()

    def is_alive(self) -> bool:
        if self._ff is None or self._ff.poll() is not None:
            return False
        return time.time() - self._last_new < max(150, self.segment_s * 15)

    def segments(self) -> list[Segment]:
        with self._lock:
            return list(self._segs)

    # ---------- interno ----------
    def _watch(self) -> None:
        lst = self.out_dir / "list.csv"
        while not self._stop.is_set():
            self._scan(lst)
            if self._ff and self._ff.poll() is not None:
                self._scan(lst)
                break
            self._stop.wait(0.5)

    def _scan(self, lst: Path) -> None:
        if not lst.exists():
            return
        try:
            rows = list(csv.reader(lst.read_text(encoding="utf-8").splitlines()))
        except OSError:
            return
        now = time.time()
        for row in rows:
            if len(row) < 3 or row[0] in self._seen:
                continue
            name, st, en = row[0], float(row[1]), float(row[2])
            self._seen.add(name)
            if self._offset is None:
                self._offset = now - en
            seg = Segment(self.out_dir / name, self._offset + st, max(en - st, 0.0))
            with self._lock:
                self._segs.append(seg)
                while len(self._segs) > self.keep:
                    self._trash.append(self._segs.pop(0).path)
                for f in list(self._trash):
                    try:
                        os.remove(f)
                        self._trash.remove(f)
                    except FileNotFoundError:
                        self._trash.remove(f)
                    except OSError:
                        pass
            self._last_new = now
            if self.on_segment:
                try:
                    self.on_segment(seg)
                except Exception:
                    log.exception("on_segment")


def loudness_per_second(path: Path) -> list[float]:
    """Volume (dB RMS) de cada segundo do arquivo: pega grito, que a média de 10 s esconderia."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                            "aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1:"
                            "measure_overall=RMS_level:measure_perchannel=none,"
                            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=30,
                           creationflags=NO_WINDOW)
        out = []
        for m in re.finditer(r"RMS_level=(-?[\d.]+|-inf)", r.stdout):
            v = m.group(1)
            out.append(-90.0 if v == "-inf" else max(float(v), -90.0))
        return out
    except Exception:
        return []


def mean_volume_db(path: Path) -> float | None:
    """Volume médio do áudio de um arquivo (dB). None se não houver áudio."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
                            "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def snapshot(url: str, out_png: Path, quality: str = "720p,720p60,480p,best") -> bool:
    """Tira um print (1280x720) da live para configurar a webcam."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        src = subprocess.Popen([sys.executable, "-m", "streamlink", "--stdout", url, quality],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", "pipe:0", "-frames:v", "1",
                            "-vf", "scale=1280:720", str(out_png)], stdin=src.stdout, timeout=60,
                           creationflags=NO_WINDOW)
        src.kill()
        return r.returncode == 0 and out_png.exists()
    except Exception:
        return False


def frame_from_file(video: Path, out_png: Path, at_s: float = 0.0) -> bool:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at_s:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=1280:720", str(out_png)], creationflags=NO_WINDOW)
    return r.returncode == 0 and out_png.exists()
