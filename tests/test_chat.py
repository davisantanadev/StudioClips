import json

from clipador.chat.kick import parse_event
from clipador.chat.twitch import parse_privmsg


def test_twitch_privmsg_with_tags():
    line = ("@badge-info=;color=#FF0000;display-name=Fulano :fulano!fulano@fulano.tmi.twitch.tv "
            "PRIVMSG #alanzoka :KEKW KEKW que isso")
    assert parse_privmsg(line) == "KEKW KEKW que isso"


def test_twitch_non_message():
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan123 :Welcome") is None


def test_kick_chat_event():
    raw = json.dumps({"event": "App\\Events\\ChatMessageEvent",
                      "data": json.dumps({"id": "1", "content": "OMEGALUL", "sender": {"username": "x"}})})
    assert parse_event(raw) == "OMEGALUL"


def test_kick_other_event():
    assert parse_event(json.dumps({"event": "pusher:connection_established", "data": "{}"})) is None


# ---------- Kick: a chave do chat agora é negociada (a antiga 32cbd… foi aposentada em out/2026) ----------
from clipador.chat import kick


def test_kick_le_chave_da_resposta_real():
    resp = {"data": {"connections": [{"credentials": {"app_key": "34bf7a0ff419a2a775b9", "cluster": "us2"},
                                      "provider": "pusher"}], "mode": "websocket"}, "message": "success"}
    assert kick.parse_connection(resp) == ("34bf7a0ff419a2a775b9", "us2")


def test_kick_resposta_sem_pusher_vira_none():
    assert kick.parse_connection({"data": {"connections": [{"provider": "centrifugo", "credentials": {}}]}}) is None
    assert kick.parse_connection({}) is None
    assert kick.parse_connection({"data": {"connections": [{"provider": "pusher", "credentials": {"app_key": ""}}]}}) is None


def test_kick_sem_internet_usa_chave_reserva(monkeypatch):
    def falha(*a, **k):
        raise OSError("sem rede")
    monkeypatch.setattr(kick, "_post_json", falha)
    monkeypatch.delenv("KICK_PUSHER_KEY", raising=False)
    assert kick.realtime_credentials() == (kick.FALLBACK_KEY, kick.FALLBACK_CLUSTER)
    assert kick.FALLBACK_KEY != "32cbd69e4b950bf97679"


def test_kick_chave_forcada_no_ambiente(monkeypatch):
    monkeypatch.setattr(kick, "_post_json", lambda *a, **k: {"data": {"connections": []}})
    monkeypatch.setenv("KICK_PUSHER_KEY", "abc123")
    assert kick.realtime_credentials() == ("abc123", kick.FALLBACK_CLUSTER)


def test_kick_url_do_websocket():
    assert kick.ws_url("k1", "eu") == "wss://ws-eu.pusher.com/app/k1?protocol=7&client=js&version=8.4.0&flash=false"