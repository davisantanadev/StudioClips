from clipador import ai, texts
from clipador.ai import Seg, Transcript, _words_into_segments
from clipador.texts import fit_card, make_texts, translate_transcript

M = "llama"


def test_fit_card_two_lines_max():
    out = fit_card(["Alanzoka roubou um banco inteiro sozinho", "e a polícia chegou com helicóptero",
                    "e ele ainda fugiu de moto pelo mar"])
    assert len(out) == 2 and all(len(l) <= 42 for l in out)


def test_make_texts_uses_llm_and_forces_tags(monkeypatch):
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {
        "card": ["Cellbit caiu do prédio", "e sobreviveu", "linha extra demais"],
        "title": "Cellbit desafiou a física 😂", "description": "Inacreditável.",
        "hashtags": ["#gta6", "#humor", "cellbit"]})
    t = make_texts("Cellbit", "twitch", "caramba", "GTA 6 dia 1", ["#viral", "#fyp"], M)
    assert len(t.card) <= 2 and all(len(l) <= 42 for l in t.card)
    assert t.hashtags[0] == "#GTA6" and "#Cellbit" in t.hashtags
    assert sum(h.lower() == "#gta6" for h in t.hashtags) == 1
    assert "#viral" in t.hashtags
    assert "Live de Cellbit na Twitch" in t.description


def test_make_texts_fallback_when_llm_down(monkeypatch):
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: None)
    t = make_texts("xQc", "kick", "", "", [], M)
    assert t.card and "xQc" in t.card[0]
    assert "#GTA6" in t.hashtags and "#xQc" in t.hashtags


def test_translate_keeps_times(monkeypatch):
    tr = Transcript("en", [Seg(0, 1.5, "oh my god"), Seg(1.5, 3, "no way")])
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {"itens": [
        {"i": 0, "texto": "meu deus"}, {"i": 1, "texto": "não acredito"}]})
    out = translate_transcript(tr, M)
    assert [s.text for s in out.segments] == ["meu deus", "não acredito"]
    assert [(s.start, s.end) for s in out.segments] == [(0, 1.5), (1.5, 3)]


def test_translate_bad_json_returns_original(monkeypatch):
    tr = Transcript("de", [Seg(0, 1, "nein")])
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {"itens": "x"})
    assert translate_transcript(tr, M) is tr


def test_words_assigned_to_segments():
    segs = _words_into_segments(
        [{"start": 0, "end": 2, "text": " olha isso"}, {"start": 2, "end": 4, "text": " meu deus"}],
        [{"word": "olha", "start": 0.1, "end": 0.5}, {"word": "isso", "start": 0.6, "end": 1.0},
         {"word": "meu", "start": 2.1, "end": 2.4}, {"word": "deus", "start": 2.5, "end": 3.0}])
    assert [w.text for w in segs[1].words] == ["meu", "deus"]


def test_transcript_json_roundtrip():
    tr = Transcript("pt", [Seg(0, 1, "oi", [ai.Word(0, 1, "oi")])])
    assert Transcript.from_json(tr.to_json()) == tr


def test_no_key_means_no_groq(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert ai.chat_json("s", "u", M) is None


def test_groq_retry_resends_audio(tmp_path, monkeypatch):
    import httpx
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"AUDIO" * 10)
    sent = []

    def fake_post(url, **kw):
        sent.append(kw["files"]["file"][1])
        req = httpx.Request("POST", url)
        if len(sent) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, request=req)
        return httpx.Response(200, json={"language": "english", "segments": [
            {"start": 0, "end": 1, "text": "hi"}], "words": [{"start": 0, "end": 1, "word": "hi"}]},
            request=req)

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(ai.httpx, "post", fake_post)
    monkeypatch.setattr(ai.httpx, "get", lambda url, **kw: httpx.Response(500, request=httpx.Request("GET", url)))
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    t = ai.transcribe_groq(audio, None, "whisper-large-v3-turbo")
    assert t.lang == "en" and t.segments[0].words[0].text == "hi"
    assert sent == [b"AUDIO" * 10] * 2


def test_cringe_card_and_title_are_cleaned(monkeypatch):
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {
        "card": ["Cellbit terminou o treino", "e o chat explodiu no banho"],
        "title": "Cellbit grita e o chat foi à loucura!", "description": "O chat foi à loucura. Fuga absurda de moto.",
        "hashtags": ["#fuga", "#chat"]})
    t = make_texts("Cellbit", "twitch", "caramba", "", [], M)
    assert t.card == ["Cellbit no GTA 6"]                      # card brega recusado
    assert t.title.startswith("GTA 6: ") and t.title.endswith("| Cellbit") and "chat" not in t.title.lower()
    assert "chat" not in t.description.lower() and "Fuga absurda de moto." in t.description
    assert t.hashtags[:4] == ["#GTA6", "#GTAVI", "#Cellbit", "#cortes"] and "#chat" not in t.hashtags


def test_good_title_is_kept_and_normalized():
    from clipador.texts import polish_title
    assert polish_title("GTA 6: o assalto mais desastroso de Vice City 😂 | Alanzoka", [], "Alanzoka") == \
        "GTA 6: O assalto mais desastroso de Vice City 😂 | Alanzoka"
    assert polish_title("Esse jumpscare ninguém esperava 😱", [], "xQc") == "GTA 6: Esse jumpscare ninguém esperava 😱 | xQc"


# ---------- legenda em PT-BR natural e textos sem cara de robô ----------
def test_fala_em_ingles_no_meio_de_live_brasileira_tambem_e_traduzida(monkeypatch):
    seen = {}

    def fake(system, user, model, **k):
        seen["user"] = user
        return {"itens": [{"i": 1, "texto": "oi, eu te odeio"}]}
    monkeypatch.setattr(ai, "chat_json", fake)
    tr = Transcript("pt", [Seg(0, 2, "o cara tá com um arpão"), Seg(2, 4, "Hi, I hate you. I wanna talk to you"),
                           Seg(4, 6, "ele quer me roubar")])
    out = translate_transcript(tr, M)
    assert [s.text for s in out.segments] == ["o cara tá com um arpão", "oi, eu te odeio", "ele quer me roubar"]
    assert "arpão" not in seen["user"]           # só manda pra IA o que não está em português


def test_traducao_mantem_primeira_pessoa_e_girias(monkeypatch):
    seen = {}

    def fake(system, user, model, **k):
        seen["prompt"] = system + user
        return {"itens": [{"i": 0, "texto": "mano, eu morri de novo"}]}
    monkeypatch.setattr(ai, "chat_json", fake)
    translate_transcript(Transcript("en", [Seg(0, 1, "bro I died again")]), M)
    p = seen["prompt"].lower()
    assert "primeira pessoa" in p and "mano" in p and "terceira pessoa" in p


def test_traducao_que_volta_faltando_tenta_de_novo(monkeypatch):
    calls = []

    def fake(system, user, model, **k):
        calls.append(1)
        if len(calls) == 1:
            return {"itens": [{"i": 0, "texto": "meu deus"}]}          # faltou um
        return {"itens": [{"i": 0, "texto": "meu deus"}, {"i": 1, "texto": "nem a pau"}]}
    monkeypatch.setattr(ai, "chat_json", fake)
    out = translate_transcript(Transcript("en", [Seg(0, 1, "oh my god"), Seg(1, 2, "no way")]), M)
    assert [s.text for s in out.segments] == ["meu deus", "nem a pau"] and len(calls) == 2


def test_textos_pedem_tom_informal_e_proibem_narracao():
    from clipador.selector import CARD_RULES, TITLE_RULES
    rules = (CARD_RULES + TITLE_RULES).lower()
    assert "terceira pessoa" in rules and "informal" in rules


def test_descricao_com_cara_de_robo_e_cortada(monkeypatch):
    monkeypatch.setattr(ai, "chat_json", lambda *a, **k: {
        "card": ["O ALARME TOCOU", "E O DONO TAVA EM CASA"], "title": "o dono tava em casa 💀",
        "description": "O alarme tocou e o dono apareceu na hora 💀 A situação sai do controle e deixa todo "
                       "mundo em choque. A queda mostra como a jogabilidade pode ser imprevisível.",
        "hashtags": []})
    t = make_texts("Alanzoka", "twitch", "tocou o alarme", "", [], M)
    first = t.description.split("\n")[0]
    assert first == "O alarme tocou e o dono apareceu na hora 💀"