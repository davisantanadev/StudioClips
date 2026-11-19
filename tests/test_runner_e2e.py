"""Ponta a ponta: live fake → gravação → chat explode → clipe pronto no banco."""
import threading
import time

import pytest

from clipador import ai, db, runner as runner_mod
from clipador.chat import ChatReader
from clipador.config import Channel, Settings, Streamer
from clipador.recorder import LiveInfo, Recorder
from tests.helpers import fake_live_cmd

pytestmark = pytest.mark.slow


class FakeChat(ChatReader):
    """5 msg/s por 35 s, depois 40 msg/s de KEKW por 8 s, depois calmo."""

    def _run(self):
        t0 = time.time()
        while not self._stop.is_set():
            el = time.time() - t0
            rate = 40 if 35 <= el < 43 else 5
            for _ in range(rate // 5):
                self.on_msg(time.time(), "KEKW KEKW" if rate > 5 else "oi")
            time.sleep(0.2)


def test_live_to_clip(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(ai, "transcribe_local", lambda *a, **k: None)
    ch = Channel("twitch", "fake")
    monkeypatch.setattr(runner_mod, "probe", lambda c: LiveInfo(c, "GTA 6 DIA 1", "Grand Theft Auto VI"))
    monkeypatch.setattr(runner_mod, "make_reader", lambda c, cb: FakeChat(c, cb))
    orig_init = Recorder.__init__
    monkeypatch.setattr(Recorder, "__init__", lambda self, *a, **k: orig_init(
        self, *a, **{**k, "source_cmd": fake_live_cmd(85)}))

    settings = Settings(data_dir=tmp_path, cooldown_s=120, analysis_after_s=20, auto_discover=False)  # sem internet
    db.init(tmp_path / "c.db")
    s = Streamer("fake", "Fake", "pt", [ch], webcam=(900, 450, 360, 250))
    r = runner_mod.Runner(settings, [s])
    r.start()
    try:
        deadline = time.time() + 150
        while time.time() < deadline and not db.list_clips():
            time.sleep(1)
    finally:
        r.stop()
    clips = db.list_clips()
    assert len(clips) == 1, list(r.log)
    c = clips[0]
    assert c.card == ["Fake no GTA 6"]
    assert 40 <= c.duration <= 60
    assert (tmp_path / c.video_path).exists()
