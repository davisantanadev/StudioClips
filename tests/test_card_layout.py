from clipador.render import CARD_H, card_font_size, card_height, card_rows


def test_long_lines_are_split_and_font_stays_big():
    card = ["Alanzoka tenta usar o celular na missão", "e solta 'Eu vou família' no meio do nada"]
    rows = card_rows(card)
    assert 3 <= len(rows) <= 4 and all(len(r) <= 28 for r, _ in rows)
    assert {src for _, src in rows} == {0, 1}          # sabe de qual frase veio (cor da 2ª linha)
    assert card_font_size([r for r, _ in rows]) >= 52  # antes caía pra 34 px
    assert card_height(card) > CARD_H                  # o card cresce pra caber


def test_short_card_keeps_two_rows():
    rows = card_rows(["Cellbit caiu do prédio", "e sobreviveu"])
    assert [r for r, _ in rows] == ["Cellbit caiu do prédio", "e sobreviveu"]
    assert card_height(["Cellbit caiu do prédio", "e sobreviveu"]) == CARD_H
