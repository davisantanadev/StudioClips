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
