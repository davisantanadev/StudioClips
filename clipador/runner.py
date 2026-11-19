"""Orquestra tudo: vigia quem está ao vivo, grava, detecta hype e manda clipes para a fila."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .chat import ChatReader, make_reader
from .config import Settings, Streamer, load_config
from .cutter import cut_clip
from .detector import HypeDetector, describe
from .pipeline import new_workdir, process_candidate
from .recorder import LiveInfo, Recorder, loudness_per_second, probe

log = logging.getLogger(__name__)
SEGMENT_S = 10
KEEP_SEGMENTS = 36  # 6 min: o momento não some do buffer enquanto espera na fila
PARALLEL_JOBS = 2   # clipes editados ao mesmo tempo


def _cleanup_old_buffers(root, keep_s: float = 900) -> None:
    """Apaga buffers de sessões antigas (>15 min), sem tocar no que um clipe na fila pode usar."""
    import shutil
    if not root.exists():
        return
    for d in root.iterdir():
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > keep_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@dataclass
class Job:
    worker: "StreamerWorker"
    peak_ts: float
    created: float = field(default_factory=time.time)
    manual: bool = False
    signals: dict = field(default_factory=dict)


class StreamerWorker:
    """Uma live sendo gravada: buffer + chat + detector."""

    def __init__(self, streamer: Streamer, info: LiveInfo, settings: Settings, enqueue) -> None:
        self.streamer = streamer
        self.info = info
        self.settings = settings
        self.enqueue = enqueue
        self.detector = HypeDetector(settings.cooldown_s, settings.hype_threshold)
        self.chat_log: deque[tuple[float, str]] = deque(maxlen=6000)  # ~2-3 min de chat rápido
        self.msgs_total = 0
        self.started = time.time()
        buf_root = settings.data_dir / "buffer" / streamer.name
        _cleanup_old_buffers(buf_root)
        self.recorder = Recorder(info.channel.url, buf_root / time.strftime("%Y%m%d-%H%M%S"),
                                 settings.quality, SEGMENT_S, KEEP_SEGMENTS, on_segment=self._on_segment)
        self.chat: ChatReader = make_reader(info.channel, self._on_msg)
        self._stop = threading.Event()

    def _on_msg(self, ts: float, text: str) -> None:
        self.msgs_total += 1
        self.chat_log.append((ts, text))
        self.detector.feed_chat(ts, text)

    def _on_segment(self, seg) -> None:
        for i, db_ in enumerate(loudness_per_second(seg.path)):
            self.detector.feed_audio(seg.start_ts + i + 1, db_)

    def start(self) -> None:
        self.recorder.start()
        self.chat.start()
        threading.Thread(target=self._loop, daemon=True, name=f"det-{self.streamer.name}").start()
        log.info("🔴 gravando %s (%s) — %s", self.streamer.display, self.info.channel.platform, self.info.title)

    def stop(self) -> None:
        self._stop.set()
        self.chat.stop()
        self.recorder.stop()

    def alive(self) -> bool:
        return self.recorder.is_alive() or time.time() - self.started < 60

    def chat_around(self, ts: float, before: float = 20, after: float = 10) -> list[str]:
        return [t for (x, t) in list(self.chat_log) if ts - before <= x <= ts + after]

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            peak = self.detector.check()
            if peak is not None:
                self.enqueue(Job(self, peak, signals=dict(self.detector.last_signals)))

    def status(self) -> dict:
        segs = self.recorder.segments()
        return {"name": self.streamer.name, "display": self.streamer.display,
                "platform": self.info.channel.platform, "title": self.info.title,
                "category": self.info.category, "buffer_s": round(sum(s.dur for s in segs)),
                "chat_msgs": self.msgs_total, "since": self.started}


class Runner:
    def __init__(self, settings: Settings, streamers: list[Streamer]) -> None:
        self.settings = settings
        self.streamers = {s.name: s for s in streamers}
        self.workers: dict[str, StreamerWorker] = {}
        self.jobs: "queue.Queue[Job]" = queue.Queue()
        self.processing: list[str] = []  # (sem limite por hora: cada streamer tem só o intervalo mínimo)
        self.log: deque[str] = deque(maxlen=60)
        self._stop = threading.Event()
        self._wake = threading.Event()  # acorda o vigia na hora (Ligar, marcar streamer)
        self._lock = threading.Lock()

    # ---------- API p/ o painel ----------
    def note(self, msg: str) -> None:
        log.info(msg)
        self.log.appendleft(f"{time.strftime('%H:%M:%S')} {msg}")

    def reload(self) -> None:
        _, streamers = load_config()
        for s in streamers:
            self.streamers[s.name] = s
            if s.name in self.workers:
                self.workers[s.name].streamer = s

    def force_clip(self, name: str) -> bool:
        w = self.workers.get(name)
        if not w:
            return False
        # o botão é apertado depois do momento: pico = ~10 s atrás
        self.jobs.put(Job(w, w.detector.force() - 10, manual=True, signals={"kind": "manual"}))
        self.note(f"✂️ corte manual pedido: {w.streamer.display}")
        return True

    def status(self) -> dict:
        return {"live": [w.status() for w in list(self.workers.values())],
                "queue": self.jobs.qsize(), "processing": list(self.processing),
                "log": list(self.log), "watching": len([s for s in self.streamers.values() if s.enabled]),
                "auto": bool(self.settings.auto_clip)}

    def _drop(self, name: str, msg: str | None = None) -> None:
        """Para de gravar um streamer (sem mexer em clipe que já está na fila sendo editado)."""
        with self._lock:
            w = self.workers.pop(name, None)
        if w:
            w.stop()
            if msg:
                self.note(msg.format(w.streamer.display))

    def set_auto(self, on: bool) -> None:
        """Botão Parar/Ligar: parado = não grava nem corta ninguém (libera internet e PC)."""
        self.settings.auto_clip = bool(on)
        if not on:
            for name in list(self.workers):
                self._drop(name)
            self.note("⏸ cortes automáticos PARADOS")
        else:
            self.note("▶ cortes automáticos LIGADOS — procurando quem está ao vivo")
        self._wake.set()

    def set_enabled(self, changes: dict[str, bool]) -> None:
        """Escolha de quais streamers clipar: desmarcado para de gravar na hora."""
        for name, on in changes.items():
            s = self.streamers.get(name)
            if not s:
                continue
            s.enabled = bool(on)
            if not on:
                self._drop(name, "⏹ {} desmarcado — parou de gravar")
        self._wake.set()

    # ---------- ciclo ----------
    def start(self) -> None:
        threading.Thread(target=self._watch_loop, daemon=True, name="vigia").start()
        for i in range(PARALLEL_JOBS):
            threading.Thread(target=self._job_loop, daemon=True, name=f"fila-{i}").start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for w in list(self.workers.values()):
            w.stop()

    def _enqueue(self, job: Job) -> None:
        why = describe(job.signals)
        self.note(f"🔥 {job.worker.streamer.display}: {job.signals.get('kind') or 'momento'}"
                  + (f" ({why})" if why else "") + " — na fila")
        self.jobs.put(job)

    def _probe_streamer(self, s: Streamer) -> LiveInfo | None:
        for ch in s.channels:
            info = probe(ch)
            if info:
                return info
        return None

    def _watch_loop(self) -> None:
        pool = ThreadPoolExecutor(max_workers=6)
        while not self._stop.is_set():
            self._wake.clear()
            # encerra lives que acabaram
            for name, w in list(self.workers.items()):
                if not w.alive():
                    self._drop(name, "⚫ {} saiu do ar")
            if not self.settings.auto_clip:  # parado: não procura ninguém até apertar Ligar
                self._wake.wait(60)
                continue
            candidates = [s for s in self.streamers.values() if s.enabled and s.name not in self.workers]
            for s, info in zip(candidates, pool.map(self._probe_streamer, candidates)):
                if info is None:
                    continue
                if self.settings.require_gta and not info.is_gta:
                    continue
                # o usuário pode ter parado ou desmarcado enquanto checávamos quem está ao vivo
                if not self.settings.auto_clip or not self.streamers[s.name].enabled:
                    continue
                if len(self.workers) >= self.settings.max_live:
                    self.note(f"limite de {self.settings.max_live} lives — {s.display} ficou de fora")
                    continue
                w = StreamerWorker(s, info, self.settings, self._enqueue)
                try:
                    w.start()
                    with self._lock:
                        self.workers[s.name] = w
                    self.note(f"🔴 {s.display} ao vivo ({info.channel.platform}) — gravando")
                except Exception as e:
                    self.note(f"erro ao gravar {s.display}: {e}")
            self._wake.wait(60)

    def _job_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            w = job.worker
            # espera o "depois do pico" entrar no buffer
            after = self.settings.analysis_after_s
            ready_at = job.peak_ts + after + SEGMENT_S + 2
            while time.time() < ready_at and not self._stop.is_set():
                time.sleep(0.5)
            segs = w.recorder.segments()
            if not segs:
                self.note(f"buffer vazio em {w.streamer.display}, clipe descartado")
                continue
            if job.peak_ts < segs[0].start_ts:  # fila atrasou e o momento já saiu do buffer
                self.note(f"momento de {w.streamer.display} saiu do buffer (fila cheia), clipe descartado")
                continue
            label = w.streamer.display
            self.processing.append(label)
            try:
                wd = new_workdir(self.settings, w.streamer)
                before = self.settings.analysis_before_s
                raw, dur, peak_off = cut_clip(segs, job.peak_ts, before, after, before + after, wd / "analysis.mp4")
                win_start = job.peak_ts - peak_off
                chat = [(t - win_start, x) for (t, x) in list(w.chat_log) if win_start <= t <= win_start + dur]
                clip = process_candidate(w.streamer, raw, peak_off, self.settings, platform=w.info.channel.platform,
                                         live_title=w.info.title, chat=chat, workdir=wd, manual=job.manual,
                                         note=self.note, signals=job.signals)
                if clip is None:
                    continue
                self.note(f"✅ pronto: {clip.display} — {clip.title} ({time.time() - job.peak_ts:.0f}s após o momento)")
            except Exception as e:
                log.exception("falha no clipe")
                self.note(f"❌ erro no clipe de {label}: {e}")
            finally:
                self.processing.remove(label)
