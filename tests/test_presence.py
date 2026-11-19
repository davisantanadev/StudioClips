"""Fechar a aba encerra o Clipador; recarregar não."""
from fastapi.testclient import TestClient

from clipador import db
from clipador.config import Settings
from clipador.web.app import create_app
from clipador.web.presence import FIRST_S, GRACE_S, IDLE_S, Presence


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_close_tab_exits_after_grace():
    c = Clock()
    p = Presence(c)
    p.seen()
    c.t += 3
    p.bye()
    c.t += GRACE_S - 1
    assert p.reason_to_exit() is None
    c.t += 2
    assert p.reason_to_exit() == "aba do painel fechada"


def test_reload_does_not_exit():
    c = Clock()
    p = Presence(c)
    p.seen()
    p.bye()          # recarregar dispara o "tchau"...
    c.t += 1
    p.seen()         # ...mas a página nova volta a falar
    c.t += GRACE_S + 5
    assert p.reason_to_exit() is None


def test_silent_tab_and_never_opened():
    c = Clock()
    p = Presence(c)
    c.t += FIRST_S + 1
    assert p.reason_to_exit() == "ninguém abriu o painel"
    p.seen()
    c.t += IDLE_S - 10
    assert p.reason_to_exit() is None
    c.t += 20
    assert p.reason_to_exit() == "painel sem resposta"


def test_api_calls_count_as_presence_and_bye_endpoint(tmp_path):
    s = Settings(data_dir=tmp_path)
    db.init(tmp_path / "c.db")
    p = Presence()
    c = TestClient(create_app(s, presence=p))
    c.get("/api/status")
    assert p.last_seen is not None
    before = p.last_seen
    assert c.post("/api/bye").status_code == 204
    assert p.bye_at is not None and p.last_seen == before   # o "tchau" não conta como presença
