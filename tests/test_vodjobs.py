"""Tarefas de VOD: progresso, pausar, continuar de onde parou (até depois de reiniciar) e excluir."""
import threading
import time

from clipador import vodjobs
from clipador.config import Channel, Settings, Streamer

S = Streamer("alanzoka", "Alanzoka", "pt", [Channel("twitch", "alanzoka")])


class Fake:
    """Imita yt-dlp, replay do chat e a IA. `gate` segura o 2º trecho até o teste liberar."""

    def __init__(self, n_moments=4, keep_every=1, free=None):
        self.free = free  # quantos trechos passam direto; os seguintes esperam o `gate`
        self.chat_reads = 0
        self.processed: list[float] = []
        self.n = n_moments
        self.keep_every = keep_every
        self.gate = threading.Event()
        self.gate.set()

    def install(self, monkeypatch):
        monkeypatch.setattr(vodjobs, "vod_info", lambda url: (3600.0, "GTA 6 DIA 1", "was_live"))
        monkeypatch.setattr(vodjobs, "chat_replay", self.chat_replay)
        monkeypatch.setattr(vodjobs, "pick_moments", lambda scores, n: [
            vodjobs.Moment(300.0 * (i + 1), 9 - i) for i in range(self.n)])
        monkeypatch.setattr(vodjobs, "download_section", lambda url, a, b, out: out)
        monkeypatch.setattr(vodjobs, "process_candidate", self.process)
        monkeypatch.setattr(vodjobs, "new_workdir", lambda settings, s: settings.data_dir)

    def chat_replay(self, url, progress=lambda f, n: None, stop=lambda: False, total_s=0.0):
        self.chat_reads += 1
        msgs = [(float(t), "KEKW") for t in range(0, 3600, 2)]
        progress(1.0, len(msgs))
        return msgs

    def process(self, streamer, raw, off, settings, **kw):
        if self.free is None or len(self.processed) >= self.free:
            self.gate.wait(5)
        self.processed.append(off)
        kw["note"]("🧠 teste")
        keep = len(self.processed) % self.keep_every == 0
        return object() if keep else None


def _wait(cond, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _mgr(tmp_path):
    settings = Settings(data_dir=tmp_path)
    return vodjobs.VodManager(settings, lambda: {"alanzoka": S})


def test_runs_to_end_and_shows_progress(tmp_path, monkeypatch):
    f = Fake(n_moments=4, keep_every=2)  # a IA descarta metade
    f.install(monkeypatch)
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=5)
    assert _wait(lambda: m.get(t)["state"] == "concluído")
    d = m.get(t)
    assert d["clips_done"] == 2 and d["tried"] == 4 and d["progress"] == 1.0
    assert d["title"] == "GTA 6 DIA 1" and any("teste" in line for line in d["log"])


def test_pause_then_resume_continues_where_it_stopped(tmp_path, monkeypatch):
    f = Fake(n_moments=4, free=1)
    f.gate.clear()                      # 1º trecho passa; o 2º fica parado no meio
    f.install(monkeypatch)
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=10)
    assert _wait(lambda: len(f.processed) >= 1)
    m.pause(t)
    f.gate.set()
    assert _wait(lambda: m.get(t)["state"] == "pausado")
    stopped_at = m.get(t)["next_index"]
    assert 1 <= stopped_at < 4
    m.resume(t)
    assert _wait(lambda: m.get(t)["state"] == "concluído")
    assert len(f.processed) == 4        # nenhum trecho feito duas vezes
    assert f.chat_reads == 1            # não releu o chat


def test_resume_after_restart_uses_saved_chat(tmp_path, monkeypatch):
    f = Fake(n_moments=3, free=1)
    f.gate.clear()
    f.install(monkeypatch)
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=10)
    assert _wait(lambda: len(f.processed) >= 1)
    m.pause(t)
    f.gate.set()
    assert _wait(lambda: m.get(t)["state"] == "pausado")
    done_before = len(f.processed)
    m2 = _mgr(tmp_path)                 # Clipador fechou e abriu de novo
    assert m2.get(t)["state"] == "pausado"
    m2.resume(t)
    assert _wait(lambda: m2.get(t)["state"] == "concluído")
    assert len(f.processed) == 3 and done_before < 3 and f.chat_reads == 1


def test_running_task_becomes_paused_after_restart(tmp_path, monkeypatch):
    f = Fake(n_moments=3)
    f.install(monkeypatch)
    f.gate.clear()
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=10)
    assert _wait(lambda: m.get(t)["stage"].startswith("cortando"))
    m2 = _mgr(tmp_path)                 # caiu no meio (PC desligou): volta como pausado
    assert m2.get(t)["state"] == "pausado"
    f.gate.set()


def test_delete_stops_and_removes(tmp_path, monkeypatch):
    f = Fake(n_moments=4, free=1)
    f.gate.clear()
    f.install(monkeypatch)
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=10)
    assert _wait(lambda: len(f.processed) >= 1)
    m.delete(t)
    f.gate.set()
    assert m.get(t) is None and all(x["id"] != t for x in m.list())
    time.sleep(0.5)
    assert len(f.processed) <= 2        # parou logo depois do trecho que estava no meio
    assert not list((tmp_path / "vods").glob(f"{t}*"))


def test_second_vod_waits_in_line(tmp_path, monkeypatch):
    f = Fake(n_moments=2)
    f.install(monkeypatch)
    f.gate.clear()
    m = _mgr(tmp_path)
    a = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=10)
    b = m.create("https://www.twitch.tv/videos/2", "alanzoka", top_n=10)
    assert _wait(lambda: m.get(a)["state"] == "rodando")
    assert m.get(b)["state"] == "na fila"
    f.gate.set()
    assert _wait(lambda: m.get(b)["state"] == "concluído")


def test_no_chat_uses_audio_and_can_pause_mid_download(tmp_path, monkeypatch):
    """Chat da VOD indisponível (Twitch mudou a API): usa o áudio; pausar no meio do download para na hora."""
    f = Fake(n_moments=2)
    f.install(monkeypatch)
    monkeypatch.setattr(vodjobs, "chat_replay", lambda url, progress, stop, total_s: [])
    started = threading.Event()
    downloads = []

    def audio_scores(url, tmp, total, progress, stop):
        downloads.append(1)
        started.set()
        for i in range(200):
            progress(i / 200)
            if stop():
                raise vodjobs.Stopped()
            time.sleep(0.01 if len(downloads) > 1 else 0.05)
        return [0.0] * 10

    monkeypatch.setattr(vodjobs, "audio_scores", audio_scores)
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=10)
    assert started.wait(5)
    assert _wait(lambda: "áudio" in m.get(t)["stage"])
    m.pause(t)
    assert _wait(lambda: m.get(t)["state"] == "pausado", timeout=3)   # não esperou o download acabar
    m.resume(t)
    assert _wait(lambda: m.get(t)["state"] == "concluído")
    assert len(f.processed) == 2 and any("áudio" in line for line in m.get(t)["log"])



def test_vod_of_live_still_on_air_explains_instead_of_hanging(tmp_path, monkeypatch):
    f = Fake(n_moments=2)
    f.install(monkeypatch)
    monkeypatch.setattr(vodjobs, "vod_info", lambda url: (27000.0, "AO VIVO", "is_live"))
    m = _mgr(tmp_path)
    t = m.create("https://www.twitch.tv/videos/1", "alanzoka", top_n=3)
    assert _wait(lambda: m.get(t)["state"] == "erro")
    assert "ainda está no ar" in m.get(t)["error"] and f.chat_reads == 0
