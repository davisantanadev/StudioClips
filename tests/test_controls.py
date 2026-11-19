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
    r = c.put("/api/streamers/enabled", json={"enabled": {"alanzoka": False, "kaicenat": True}})
    assert r.status_code == 200
    by = {x["name"]: x["enabled"] for x in c.get("/api/streamers").json()}
    assert by["alanzoka"] is False and by["kaicenat"] is True
    assert c.put("/api/streamers/enabled", json={"enabled": {"nao_existe": True}}).status_code == 404


def test_achados_entram_depois_dos_fixos_e_saem_quando_esfriam(tmp_path, monkeypatch):
    from clipador import discover as dc
    r = _runner(tmp_path)
    r.settings.auto_discover = True
    r.settings.discover_langs = ["pt", "en", "es"]
    found = [dc.Found("twitch", "ohnepixel", "ohnePixel", 49000, "en"), dc.Found("kick", "luken", "luken", 5400, "es")]
    monkeypatch.setattr(dc, "discover_all", lambda: list(found))
    r.refresh_discovered(now=1000.0)
    assert list(r.streamers)[:2] == ["a", "b"]                       # os da lista vêm primeiro
    assert [s.display for s in r.streamers.values() if s.auto] == ["ohnePixel", "luken"]
    r.workers[dc.auto_name("kick", "luken")] = FakeWorker()            # luken está gravando
    found[:] = [dc.Found("youtube", "UCnovo", "Novo", 8000, "pt")]
    r.refresh_discovered(now=1100.0)                                   # antes de 5 min: não procura de novo
    assert dc.auto_name("twitch", "ohnepixel") in r.streamers
    r.refresh_discovered(now=1400.0)
    autos = [s.display for s in r.streamers.values() if s.auto]
    assert "ohnePixel" not in autos                                    # esfriou e não estava gravando: sai
    assert "luken" in autos and "Novo" in autos                        # gravando continua até a live acabar


def test_descoberta_desligada_nao_procura(tmp_path, monkeypatch):
    from clipador import discover as dc
    r = _runner(tmp_path)
    r.settings.auto_discover = False
    monkeypatch.setattr(dc, "discover_all", lambda: (_ for _ in ()).throw(AssertionError("não devia procurar")))
    r.refresh_discovered(now=1000.0)
    assert not any(s.auto for s in r.streamers.values())

def test_fila_atende_o_momento_mais_forte_primeiro_e_descarta_o_mais_fraco():
    from clipador.runner import Job, JobQueue
    q = JobQueue(max_pending=3)
    w = FakeWorker()
    fraco, medio, forte = (Job(w, 1.0, signals={"score": s}) for s in (3.1, 4.5, 7.9))
    manual = Job(w, 2.0, manual=True)
    assert q.put(fraco) is None and q.put(forte) is None and q.put(medio) is None
    dropped = q.put(manual)                     # encheu: sai o mais fraco
    assert dropped is fraco and q.qsize() == 3
    assert q.get(timeout=0.1) is manual         # "Cortar agora" passa na frente
    assert q.get(timeout=0.1) is forte
    assert q.get(timeout=0.1) is medio
    assert q.get(timeout=0.05) is None


def test_clipes_da_twitch_tem_prioridade_alta():
    from clipador.runner import Job, JobQueue
    q = JobQueue(max_pending=5)
    w = FakeWorker()
    hype = Job(w, 1.0, signals={"score": 9.0})
    clipado = Job(w, 1.0, signals={"kind": "clipado na Twitch", "twitch_clips": 3})
    q.put(hype), q.put(clipado)
    assert q.get(timeout=0.1) is clipado