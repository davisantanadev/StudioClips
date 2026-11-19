"""Rodízio grátis de IA: modelos e provedores trocam sozinhos quando um bate o limite ou some."""
import json

import httpx

from clipador import ai


class FakeNet:
    """Imita Groq/Cerebras/Gemini. `limited` = (provedor, modelo) que respondem 429; `gone` = aposentados."""

    def __init__(self, models: dict[str, list[str]], limited=(), gone=(), retry_after="30"):
        self.models = models
        self.limited = set(limited)
        self.gone = set(gone)
        self.retry_after = retry_after
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def prov(url):
        return "groq" if "groq" in url else "cerebras" if "cerebras" in url else "gemini"

    def post(self, url, headers=None, timeout=None, json=None, data=None, files=None, **kw):
        p = self.prov(url)
        model = (json or data)["model"]
        self.calls.append((p, model))
        req = httpx.Request("POST", url)
        if (p, model) in self.gone or model not in self.models.get(p, []):
            return httpx.Response(404, request=req, json={"error": {"message": f"model {model} not found",
                                                                    "code": "model_not_found"}})
        if (p, model) in self.limited:
            return httpx.Response(429, request=req, headers={"retry-after": self.retry_after},
                                  json={"error": {"message": "rate limit"}})
        if files is not None:  # whisper
            return httpx.Response(200, request=req, json={"language": "portuguese", "segments": [
                {"start": 0, "end": 1, "text": "oi", "no_speech_prob": 0.01, "avg_logprob": -0.2}],
                "words": [{"word": "oi", "start": 0.1, "end": 0.5}]})
        img = isinstance(json["messages"][-1]["content"], list)
        body = {"ok": True, "prov": p, "model": model, "img": img}
        return httpx.Response(200, request=req, json={"choices": [{"message": {"content": _dumps(body)}}]})

    def get(self, url, headers=None, timeout=None):
        p = self.prov(url)
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"data": [{"id": m} for m in self.models.get(p, [])]})


def _dumps(x):
    return json.dumps(x)


def _install(monkeypatch, net, keys=("GROQ_API_KEY",)):
    for env in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    for env in keys:
        monkeypatch.setenv(env, "k_" + env)
    monkeypatch.setattr(ai.httpx, "post", net.post)
    monkeypatch.setattr(ai.httpx, "get", net.get)
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)


GROQ = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "whisper-large-v3"]


def test_rate_limited_model_rotates_immediately(monkeypatch):
    net = FakeNet({"groq": GROQ}, limited={("groq", "openai/gpt-oss-120b")})
    _install(monkeypatch, net)
    j = ai.chat_json("s", "u", "openai/gpt-oss-120b")
    assert j["model"] == "qwen/qwen3.8-27b"
    # o modelo no limite fica de lado: o próximo pedido nem tenta ele
    net.calls.clear()
    ai.chat_json("s", "u", "openai/gpt-oss-120b")
    assert net.calls == [("groq", "qwen/qwen3.8-27b")]


def test_all_groq_limited_uses_cerebras(monkeypatch):
    net = FakeNet({"groq": GROQ, "cerebras": ["gpt-oss-120b"]},
                  limited={("groq", m) for m in GROQ})
    _install(monkeypatch, net, keys=("GROQ_API_KEY", "CEREBRAS_API_KEY"))
    assert ai.chat_json("s", "u", "openai/gpt-oss-120b")["prov"] == "cerebras"


def test_retired_model_is_skipped(monkeypatch):
    net = FakeNet({"groq": GROQ})
    _install(monkeypatch, net)
    assert ai.chat_json("s", "u", "llama-3.3-70b-versatile")["model"] == "openai/gpt-oss-120b"


def test_images_go_to_a_vision_model(monkeypatch):
    net = FakeNet({"groq": GROQ, "gemini": ["gemini-2.5-flash"]}, limited={("groq", "qwen/qwen3.8-27b")})
    _install(monkeypatch, net, keys=("GROQ_API_KEY", "GEMINI_API_KEY"))
    j = ai.chat_json("s", "u", "x", images=[b"\xff\xd8fakejpeg"])
    assert j["prov"] == "gemini" and j["img"]


def test_everything_limited_returns_none_without_hanging(monkeypatch):
    net = FakeNet({"groq": GROQ}, limited={("groq", m) for m in GROQ}, retry_after="3600")
    _install(monkeypatch, net)
    assert ai.chat_json("s", "u", "openai/gpt-oss-120b") is None


def test_multiple_keys_in_one_field(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_a, gsk_b\ngsk_c")
    assert ai.keys_for(ai.PROVIDERS[0]) == ["gsk_a", "gsk_b", "gsk_c"]


def test_whisper_limited_falls_back(monkeypatch, tmp_path):
    net = FakeNet({"groq": GROQ + ["whisper-large-v3-turbo"]}, limited={("groq", "whisper-large-v3")})
    _install(monkeypatch, net)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = ai.transcribe_groq(audio, "pt", "whisper-large-v3")
    assert t.text == "oi" and net.calls[-1] == ("groq", "whisper-large-v3-turbo")


def test_parse_wait():
    assert ai.parse_wait("1m26.4s") == 86.4 and ai.parse_wait("577ms") == 0.577 and ai.parse_wait("7") == 7


def test_error_body_as_list_does_not_crash(monkeypatch):
    """Gemini/Groq às vezes mandam o erro como LISTA: [{"error": {...}}]. Antes isso derrubava o clipe."""
    calls = []

    def post(url, headers=None, timeout=None, json=None, **kw):
        calls.append(json["model"])
        req = httpx.Request("POST", url)
        if json["model"] == "openai/gpt-oss-120b":
            return httpx.Response(400, request=req, json=[{"error": {"code": 400, "message": "Invalid request",
                                                                     "status": "INVALID_ARGUMENT"}}])
        if json["model"] == "qwen/qwen3.8-27b":
            return httpx.Response(400, request=req, text="<html>bad gateway</html>")
        body = {"ok": True, "model": json["model"]}
        return httpx.Response(200, request=req, json={"choices": [{"message": {"content": _dumps(body)}}]})

    net = FakeNet({"groq": GROQ})
    _install(monkeypatch, net)
    monkeypatch.setattr(ai.httpx, "post", post)
    j = ai.chat_json("s", "u", "openai/gpt-oss-120b")
    assert j["model"] == "openai/gpt-oss-20b"
    assert calls == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]


def test_error_info_reads_any_shape():
    req = httpx.Request("POST", "http://x")
    assert ai._error_info(httpx.Response(404, request=req, json={"error": {"code": "model_not_found", "message": "M"}})) \
        == ("model_not_found", "m")
    assert ai._error_info(httpx.Response(400, request=req, json=[{"error": {"message": "Model gone"}}]))[1] == "model gone"
    assert ai._error_info(httpx.Response(400, request=req, json=["x"])) == ("", "")
    assert ai._error_info(httpx.Response(400, request=req, text="oops")) == ("", "oops")
