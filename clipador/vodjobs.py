"""Tarefas de "Clipar live gravada" (VOD) com progresso, pausar, continuar de onde parou e excluir.

Cada passo caro é salvo em disco assim que termina (chat lido, momentos achados, último trecho cortado),
então "continuar" retoma do ponto exato, até depois de fechar e abrir o Clipador.
Uma VOD roda por vez (as outras esperam na fila) pra não disputar a cota grátis da IA.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .config import Settings, Streamer
from .pipeline import new_workdir, process_candidate
from .twitchclips import ClipHit, hits_near, vod_boost
from .vod import (BUCKET, Moment, Stopped, audio_scores, heatmap_boost, vod_heatmap, chat_replay, chat_scores, download_section, pick_moments,
                  platform_of, vod_info)

log = logging.getLogger(__name__)
ACTIVE = ("na fila", "rodando")


class _Stop(Exception):
    """Pausa ou exclusão pedida: para no próximo ponto seguro."""


class VodManager:
    def __init__(self, settings: Settings, streamers: Callable[[], dict[str, Streamer]],
                 note: Callable[[str], None] = log.info) -> None:
        self.settings = settings
        self.streamers = streamers
        self.note = note
        self.dir = Path(settings.data_dir) / "vods"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "tarefas.json"
        self.tasks: dict[str, dict] = {}
        self._pause: set[str] = set()
        self._cancel: set[str] = set()
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._load()

    # ---------- persistência ----------
    def _load(self) -> None:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        for t in data if isinstance(data, list) else []:
            if t.get("state") in ACTIVE:  # caiu no meio (PC desligou, Clipador fechou): continua quando pedir
                t["state"] = "pausado"
                t["stage"] = "pausado (o Clipador foi fechado) — aperte Continuar"
            self.tasks[t["id"]] = t

    def _save(self) -> None:
        with self._lock:
            data = sorted(self.tasks.values(), key=lambda t: t["created"])
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.file)

    def _chat_file(self, tid: str) -> Path:
        return self.dir / f"{tid}_chat.json"

    def _update(self, tid: str, **kw) -> None:
        with self._lock:
            t = self.tasks.get(tid)
            if t is None:  # foi excluída
                return
            t.update(kw)
            self._save()

    def _log(self, tid: str, msg: str) -> None:
        with self._lock:
            t = self.tasks.get(tid)
            if t is None:
                return
            t["log"] = (t.get("log", []) + [f"{time.strftime('%H:%M:%S')} {msg}"])[-40:]
            self._save()
        self.note(msg)

    # ---------- API pro painel ----------
    def list(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in sorted(self.tasks.values(), key=lambda t: -t["created"])]

    def get(self, tid: str) -> dict | None:
        with self._lock:
            t = self.tasks.get(tid)
            return dict(t) if t else None

    def create(self, url: str, streamer: str, top_n: int = 6) -> str:
        tid = uuid.uuid4().hex[:10]
        with self._lock:
            self.tasks[tid] = {
                "id": tid, "url": url.strip(), "streamer": streamer, "top_n": max(1, min(int(top_n), 20)),
                "created": time.time(), "state": "na fila", "stage": "esperando a vez", "progress": 0.0,
                "title": "", "total": None, "moments": None, "next_index": 0, "clips_done": 0, "tried": 0,
                "log": [], "error": ""}
            self._save()
        self._kick()
        return tid

    def pause(self, tid: str) -> bool:
        with self._lock:
            t = self.tasks.get(tid)
            if not t or t["state"] not in ACTIVE:
                return False
            if t["state"] == "na fila":
                t["state"], t["stage"] = "pausado", "pausado"
                self._save()
            else:
                self._pause.add(tid)
                t["stage"] = "pausando… (termina o trecho atual e para)"
                self._save()
        return True

    def resume(self, tid: str) -> bool:
        with self._lock:
            t = self.tasks.get(tid)
            if not t or t["state"] not in ("pausado", "erro"):
                return False
            self._pause.discard(tid)
            t["state"], t["stage"], t["error"] = "na fila", "esperando a vez", ""
            self._save()
        self._kick()
        return True

    def delete(self, tid: str) -> bool:
        with self._lock:
            t = self.tasks.pop(tid, None)
            if t is None:
                return False
            if t["state"] == "rodando":
                self._cancel.add(tid)
            self._save()
        self._chat_file(tid).unlink(missing_ok=True)
        shutil.rmtree(Path(self.settings.data_dir) / "vod_tmp" / tid, ignore_errors=True)
        return True

    # ---------- execução ----------
    def _kick(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._loop, daemon=True, name="vods")
            self._worker.start()

    def _next(self) -> str | None:
        with self._lock:
            queued = [t for t in self.tasks.values() if t["state"] == "na fila"]
            if not queued:
                return None
            t = min(queued, key=lambda t: t["created"])
            t["state"] = "rodando"
            self._save()
            return t["id"]

    def _loop(self) -> None:
        while True:
            tid = self._next()
            if tid is None:
                return
            try:
                self._run(tid)
            except _Stop:
                with self._lock:
                    if tid in self._cancel or tid not in self.tasks:
                        self._cancel.discard(tid)
                        continue
                    self._pause.discard(tid)
                self._update(tid, state="pausado", stage="pausado — aperte Continuar pra seguir de onde parou")
            except Exception as e:
                log.exception("VOD")
                self._update(tid, state="erro", stage="deu erro — aperte Continuar pra tentar de novo",
                             error=str(e)[:300])
                self._log(tid, f"❌ {e}")

    def _check(self, tid: str) -> None:
        if tid in self._cancel or tid in self._pause or tid not in self.tasks:
            raise _Stop()

    def _run(self, tid: str) -> None:
        t = self.get(tid)
        s = self.streamers().get(t["streamer"])
        if s is None:
            raise RuntimeError(f"streamer '{t['streamer']}' não está mais na lista")
        url = t["url"]

        if not t.get("total") or t.get("moments") is None:
            self._update(tid, stage="lendo as informações da VOD", progress=0.01)
            total, title, live_status = vod_info(url)
            if live_status == "is_live":  # VOD de uma live que ainda está no ar: não dá pra baixar do começo
                raise RuntimeError("essa live ainda está no ar — ela já é cortada ao vivo (aba Ao vivo). "
                                   "Aperte Continuar depois que a live acabar")
            if total <= 0:
                raise RuntimeError("não consegui ler a VOD (o link está certo? ela é pública?)")
            self._update(tid, total=total, title=title)
            t = self.get(tid)
        total = float(t["total"])
        self._check(tid)

        msgs: list[tuple[float, str]] = []
        cache = self._chat_file(tid)
        if cache.exists():
            msgs = [tuple(x) for x in json.loads(cache.read_text(encoding="utf-8"))]
        if t.get("moments") is None:
            if not cache.exists():
                self._update(tid, stage="lendo o chat da VOD", progress=0.02)

                def chat_progress(frac: float, n: int) -> None:
                    self._update(tid, stage=f"lendo o chat da VOD: {n} mensagens ({round(frac * 100)}%)",
                                 progress=round(0.02 + 0.38 * frac, 3))

                msgs = chat_replay(url, chat_progress, stop=lambda: tid in self._pause or tid in self._cancel
                                   or tid not in self.tasks, total_s=total,
                                   workdir=Path(self.settings.data_dir) / "vod_tmp" / tid / "chat")
                self._check(tid)  # leitura interrompida: não salva chat pela metade
                cache.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
                self._log(tid, f"chat lido: {len(msgs)} mensagens")
            if len(msgs) > 200:
                scores = chat_scores(msgs, total)
            else:
                self._log(tid, "chat da VOD indisponível — vou achar os momentos pelo áudio (gritos e reações)")
                self._update(tid, stage="baixando o áudio da VOD pra achar os gritos e reações", progress=0.05)
                tmp = Path(self.settings.data_dir) / "vod_tmp" / tid
                tmp.mkdir(parents=True, exist_ok=True)

                def audio_progress(frac: float) -> None:
                    self._update(tid, stage=f"baixando o áudio da VOD: {round(frac * 100)}%",
                                 progress=round(0.05 + 0.3 * frac, 3))

                try:
                    scores = audio_scores(url, tmp, total, audio_progress, stop=lambda: tid in self._pause
                                          or tid in self._cancel or tid not in self.tasks)
                except Stopped:
                    raise _Stop()
                self._update(tid, stage="ouvindo o áudio da VOD", progress=0.37)
            self._check(tid)
            # "Mais repetidos" do YouTube: onde a galera voltou pra rever (VOD com uns dias de audiência)
            heat = vod_heatmap(url)
            if heat:
                scores = heatmap_boost(scores, heat)
                self._log(tid, "🔁 \"Mais repetidos\" do YouTube entra como sinal (onde a galera mais reviu)")
            # clipes que os espectadores fizeram na Twitch (se tiver as chaves da Twitch): mais peso nesses trechos
            scores, hits = vod_boost(url, scores, BUCKET, note=lambda msg: self._log(tid, msg))
            found = sorted(pick_moments(scores, t["top_n"] * 2), key=lambda m: -m.score)  # dobro: IA descarta fracos
            self._update(tid, moments=[[m.t, m.score] for m in found], progress=0.4,
                         twitch_hits=[[h.t, h.views] for h in hits])
            self._log(tid, f"{len(found)} momentos candidatos achados" if found else "nenhum pico forte nessa VOD")
            t = self.get(tid)

        moments = [Moment(a, b) for a, b in t["moments"]]
        hits = [ClipHit(a, b) for a, b in t.get("twitch_hits") or []]
        before, after = self.settings.analysis_before_s, self.settings.analysis_after_s
        for k in range(t["next_index"], len(moments)):
            t = self.get(tid)
            if t is None:
                raise _Stop()
            if t["clips_done"] >= t["top_n"]:
                break
            self._check(tid)
            m = moments[k]
            self._update(tid, stage=f"cortando trecho {k + 1} de {len(moments)} "
                                    f"({time.strftime('%H:%M:%S', time.gmtime(m.t))} da VOD)",
                         progress=round(0.4 + 0.6 * k / len(moments), 3))
            start, end = max(0.0, m.t - before), min(total, m.t + after)
            clip = None
            try:
                wd = new_workdir(self.settings, s)
                raw = download_section(url, start, end, wd / "analysis.mp4")
                chat = [(x - start, txt) for (x, txt) in msgs if start <= x <= end]
                n_clips, views = hits_near(hits, m.t)
                clip = process_candidate(s, raw, m.t - start, self.settings, platform=platform_of(url),
                                         live_title=t["title"], chat=chat, workdir=wd,
                                         note=lambda msg: self._log(tid, msg),
                                         signals={"twitch_clips": n_clips, "twitch_views": views} if n_clips else None)
            except _Stop:
                raise
            except Exception as e:
                log.exception("VOD trecho")
                self._log(tid, f"❌ trecho {k + 1} falhou: {e}")
            t = self.get(tid)
            if t is None:
                raise _Stop()
            done = t["clips_done"] + (clip is not None)
            self._update(tid, next_index=k + 1, tried=t["tried"] + 1, clips_done=done)
            if clip is not None:
                self._log(tid, f"✅ clipe {done} de {t['top_n']} pronto")
        t = self.get(tid)
        if t is None:
            raise _Stop()
        self._update(tid, state="concluído", progress=1.0,
                     stage=f"pronto: {t['clips_done']} clipe(s) de {t['tried']} momento(s) analisados")
