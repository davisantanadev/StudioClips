import pytest

from clipador import ai, db, pipeline
from clipador.ai import Seg, Transcript, Word
from clipador.config import Channel, Settings, Streamer
from tests.helpers import make_video

pytestmark = pytest.mark.slow


def _fake_transcribe(lang):
    def f(path, hint, *a):
        words = [Word(i * 0.4, i * 0.4 + 0.3, w) for i, w in enumerate("oh my god no way".split())]
        return Transcript(lang, [Seg(0, 2, "oh my god no way", words)])
    return f


def test_process_clip_foreign_end_to_end(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, handle="@teste")
    db.init(tmp_path / "c.db")
    monkeypatch.setattr(pipeline, "transcribe", _fake_transcribe("en"))

    def fake_chat(system, user, model, temperature=0.7):
        if "Traduza" in user:
            return {"itens": [{"i": 0, "texto": "meu deus, não acredito"}]}
        return {"card": ["xQc bateu o carro", "no primeiro minuto"], "title": "xQc no GTA 6 😂",
                "description": "Que isso.", "hashtags": ["#xqc", "#gta"]}
    monkeypatch.setattr(ai, "chat_json", fake_chat)

    s = Streamer("xqc", "xQc", "en", [Channel("kick", "xqc")], webcam=(900, 450, 360, 250))
    src = make_video(tmp_path / "raw.mp4", 6)
    clip = pipeline.process_clip(s, src, 3.0, settings, platform="kick", live_title="GTA 6 DAY 1")
    assert clip.id and clip.card == ["xQc bateu o carro", "no primeiro minuto"]
    assert clip.transcript == "meu deus, não acredito"
    assert (tmp_path / clip.video_path).exists() and (tmp_path / clip.cover_path).exists()
    assert "Phrase" in (tmp_path / clip.workdir / "subs.ass").read_text(encoding="utf-8")
    assert db.list_clips()[0].title == "GTA 6: xQc no GTA 6 😂 | xQc"

    new = pipeline.rerender_card(clip.id, ["Card editado", "na mão"], settings)
    assert new.card == ["Card editado", "na mão"]


def test_process_clip_without_ai(tmp_path, monkeypatch):
    """Sem Groq e sem Whisper local: sai o clipe com texto padrão e sem legenda."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(ai, "transcribe_local", lambda *a, **k: None)
    settings = Settings(data_dir=tmp_path)
    db.init(tmp_path / "c.db")
    s = Streamer("alanzoka", "Alanzoka", "pt", [Channel("twitch", "alanzoka")])
    clip = pipeline.process_clip(s, make_video(tmp_path / "raw.mp4", 4), 2.0, settings, platform="twitch")
    assert clip.card == ["Alanzoka no GTA 6"]
    assert "#GTA6" in clip.hashtags and "#Alanzoka" in clip.hashtags
    assert (tmp_path / clip.video_path).exists()


def test_candidate_ai_picks_segment_and_rejects_weak(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, handle="@teste")
    db.init(tmp_path / "c.db")
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: Transcript("pt", [
        Seg(i, i + 4, f"frase {i}", [Word(i, i + 1, "frase"), Word(i + 1, i + 2, str(i))]) for i in range(0, 80, 5)]))
    nota = {"v": 8}
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {
        "inicio": 20, "fim": 65, "nota": nota["v"], "motivo": "teste",
        "card": ["QUEDA DO PRÉDIO", "MAIS ABSURDA DE VICE CITY"], "titulo": "Que queda 😂",
        "descricao": "Inacreditável.", "hashtags": ["#queda"]})
    s = Streamer("alanzoka", "Alanzoka", "pt", [Channel("twitch", "alanzoka")])
    clip = pipeline.process_candidate(s, make_video(tmp_path / "an.mp4", 80), 50, settings, platform="twitch",
                                      chat=[(50, "KEKW")] * 20)
    assert clip.card == ["QUEDA DO PRÉDIO", "MAIS ABSURDA DE VICE CITY"]
    assert clip.title == "GTA 6: Que queda 😂 | Alanzoka"
    assert 40 <= clip.duration <= 60
    assert clip.transcript.startswith("frase 20")
    nota["v"] = 3
    assert pipeline.process_candidate(s, make_video(tmp_path / "an2.mp4", 80), 50, settings) is None
    assert len(db.list_clips()) == 1
