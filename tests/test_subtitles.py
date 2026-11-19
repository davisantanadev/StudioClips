from clipador.ai import Seg, Transcript, Word
from clipador.subtitles import ass_color, build_ass


def _dialogues(ass):
    return [l for l in ass.splitlines() if l.startswith("Dialogue:")]


def test_karaoke_one_event_per_word_grouped_by_3():
    words = [Word(i * 0.4, i * 0.4 + 0.35, w) for i, w in enumerate("olha isso aqui mano que loucura total".split())]
    t = Transcript("pt", [Seg(0, 3, "olha isso aqui mano que loucura total", words)])
    d = _dialogues(build_ass(t, "karaoke", "#FF2E63"))
    assert len(d) == 7
    assert "OLHA ISSO AQUI" in d[0].replace("{\\c&H00632EFF\\fscx112\\fscy112}", "").replace("{\\r}", "")
    assert "\\c&H00632EFF" in d[0]
    assert d[0].split(",")[1] == "0:00:00.00"


def test_phrase_mode_max_two_lines():
    t = Transcript("pt", [Seg(0, 6, "isso aqui é uma frase bem comprida traduzida do inglês que não cabe em duas linhas de jeito nenhum")])
    d = _dialogues(build_ass(t, "phrase"))
    assert len(d) >= 2
    assert all(x.count("\\N") <= 1 for x in d)


def test_empty_transcript_valid_ass():
    ass = build_ass(Transcript("pt", []), "karaoke")
    assert "[Events]" in ass and _dialogues(ass) == []


def test_karaoke_without_words_falls_back_to_phrase():
    d = _dialogues(build_ass(Transcript("pt", [Seg(0, 2, "meu deus")]), "karaoke"))
    assert len(d) == 1 and "Phrase" in d[0]


def test_ass_color():
    assert ass_color("#FF2E63") == "&H00632EFF"
