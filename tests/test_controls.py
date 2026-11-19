"""Ligar/parar os cortes automáticos e escolher quais streamers clipar."""
from fastapi.testclient import TestClient

from clipador import db
from clipador.config import Channel, Settings, Streamer, load_config, load_user_settings, save_streamers_enabled
from clipador.runner import Runner
from clipador.web.app import create_app


class FakeWorker:
    def __init__(self):
        self.stopped = False
        self.streamer = Streamer("x", "X", "pt", [])

    def stop(self):
        self.stopped = True

    def status(self):
        return {"name": self.streamer.name}


def _runner(tmp_path):
    s = Settings(data_dir=tmp_path)
    st = [Streamer("a", "A", "pt", [Channel("twitch", "a")]), Streamer("b", "B", "pt", [Channel("twitch", "b")])]
    return Runner(s, st)


def test_choice_of_streamers_survives_restart(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text("streamers:\n  - {name: a, channels: [{platform: twitch, channel: a}]}\n"
                   "  - {name: b, channels: [{platform: twitch, channel: b}]}\n", encoding="utf-8")
    save_streamers_enabled({"a": False})
    _, st = load_config(cfg)
    assert {s.name: s.enabled for s in st} == {"a": False, "b": True}


def test_stop_stops_recordings_and_start_resumes(tmp_path):
    r = _runner(tmp_path)
    w = FakeWorker()
    r.workers["a"] = w
    r.set_auto(False)
    assert w.stopped and r.workers == {} and not r.settings.auto_clip
    assert r.status()["auto"] is False
    r.set_auto(True)
    assert r.settings.auto_clip and r.status()["auto"] is True


def test_unchecking_a_streamer_stops_only_him(tmp_path):
    r = _runner(tmp_path)
    wa, wb = FakeWorker(), FakeWorker()
    r.workers.update(a=wa, b=wb)
    r.set_enabled({"a": False})
    assert wa.stopped and not wb.stopped and list(r.workers) == ["b"]
    assert r.status()["watching"] == 1


def test_api_auto_and_enabled_are_saved(tmp_path):
    s = Settings(data_dir=tmp_path)
    db.init(tmp_path / "c.db")
    c = TestClient(create_app(s))
    assert c.post("/api/auto", json={"on": False}).json()["auto"] is False
    assert load_user_settings()["auto_clip"] is False and s.auto_clip is False
    r = c.put("/api/streamers/enabled", json={"enabled": {"alanzoka": False, "xqc": True}})
    assert r.status_code == 200
    by = {x["name"]: x["enabled"] for x in c.get("/api/streamers").json()}
    assert by["alanzoka"] is False and by["xqc"] is True
    assert c.put("/api/streamers/enabled", json={"enabled": {"nao_existe": True}}).status_code == 404
