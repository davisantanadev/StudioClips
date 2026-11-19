import pytest

from clipador import ai
from clipador.ai import Seg, Transcript, Word, _words_into_segments
from clipador.selector import select_moment, slice_transcript, snap


def _tr():
    segs = []
    for i in range(0, 110, 5):  # frases de 4 s a cada 5 s
        words = [Word(i + k, i + k + 0.8, f"w{i}_{k}") for k in range(4)]
        segs.append(Seg(i, i + 4, " ".join(w.text for w in words), words))
    return Transcript("pt", segs)


def test_snap_keeps_between_40_and_60_and_on_sentence_edges():
    t = _tr()
    st, en = snap(31.5, 52, t, 115, 70, 40, 60)  # IA pediu só 20 s
    assert 40 <= en - st <= 60
    assert st == 30  # puxou pro início da frase
    st, en = snap(10, 100, t, 115, 70, 40, 60)  # IA pediu 90 s
    assert en - st == 60


def test_snap_window_shorter_than_min():
    st, en = snap(0, 20, Transcript("pt", []), 30, 15, 40, 60)
    assert (st, en) == (0, 30)


def test_select_uses_ai_and_scores(monkeypatch):
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {
        "inicio": 45, "fim": 92, "nota": 8, "motivo": "fuga épica",
        "card": ["Alanzoka fugiu da polícia", "pulando de moto no mar"], "titulo": "FUGA INSANA 😱",
        "descricao": "Que isso.", "hashtags": ["#fuga"]})
    sel = select_moment(_tr(), [(70, "KEKW")] * 30, 70, 115, display="Alanzoka", live_title="",
                        min_s=40, max_s=60, model="x")
    assert sel.by_ai and sel.score == 8 and sel.card[0] == "Alanzoka fugiu da polícia"
    assert sel.start == 45 and 40 <= sel.dur <= 60


def test_select_fallback_without_ai(monkeypatch):
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: None)
    sel = select_moment(_tr(), [], 70, 115, display="X", live_title="", min_s=40, max_s=60, model="x")
    assert not sel.by_ai and sel.start <= 70 <= sel.end and 40 <= sel.dur <= 60


def test_slice_transcript_shifts_times():
    out = slice_transcript(_tr(), 30, 72)
    assert out.segments[0].start == 0 and out.segments[0].words[0].text == "w30_0"
    assert all(0 <= w.start <= 42 for s in out.segments for w in s.words)
    assert out.segments[-1].words[-1].text.startswith("w70")


def test_hallucinated_segments_dropped():
    segs = _words_into_segments(
        [{"start": 0, "end": 2, "text": "olha isso", "no_speech_prob": 0.05, "avg_logprob": -0.3},
         {"start": 2, "end": 5, "text": "Legendas pela comunidade Amara.org", "no_speech_prob": 0.9}],
        [{"word": "olha", "start": 0.1, "end": 0.5}, {"word": "Legendas", "start": 2.5, "end": 3}])
    assert [s.text for s in segs] == ["olha isso"]


def test_select_picks_best_of_several_candidates(monkeypatch):
    seen = {}

    def fake(system, user, model, **k):
        seen["user"] = user
        return {"candidatos": [
            {"inicio": 10, "fim": 55, "nota": 5, "motivo": "conversa solta", "card": ["a"], "titulo": "a"},
            {"inicio": 40, "fim": 99, "nota": 9, "motivo": "fuga completa com reação",
             "card": ["Alanzoka roubou a viatura", "e fugiu da polícia pelo mar"], "titulo": "FUGA 😱",
             "descricao": "x", "hashtags": ["#fuga"]}]}
    monkeypatch.setattr(ai, "chat_json", fake)
    sel = select_moment(_tr(), [(70, "KEKW")] * 30, 70, 115, display="Alanzoka", live_title="",
                        min_s=45, max_s=60, model="x")
    assert sel.score == 9 and sel.card[0] == "Alanzoka roubou a viatura"
    assert sel.start == 40 and 45 <= sel.dur <= 60
    # o pedido pede ~1 minuto e proíbe card genérico
    assert "60" in seen["user"] and "proibido" in seen["user"].lower() and "susto" in seen["user"].lower()


def test_generic_card_detected():
    from clipador.texts import is_generic_card
    assert is_generic_card(["Alanzoka no GTA 6"], "Alanzoka")
    assert is_generic_card(["Momento insano do Alanzoka"], "Alanzoka")
    assert not is_generic_card(["Alanzoka roubou a viatura", "e fugiu da polícia pelo mar"], "Alanzoka")
