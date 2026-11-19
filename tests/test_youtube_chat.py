"""Chat ao vivo do YouTube pelo yt-dlp (o chat-downloader quebrou com a mudança do YouTube em nov/2026)."""
import json

from clipador.chat import youtube as yt


def _line(mid, text, usec, emoji=None):
    runs = [{"text": text}] + ([{"emoji": {"emojiId": emoji, "shortcuts": [":joy:"]}}] if emoji else [])
    return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {
        "liveChatTextMessageRenderer": {"id": mid, "message": {"runs": runs}, "timestampUsec": str(usec),
                                        "authorName": {"simpleText": "@x"}}}}}]}})


def test_le_mensagem_e_horario():
    got = yt.parse_line(_line("a1", "KKKKKKK", 1_795_000_000_500_000, emoji="😂"))
    assert got == [("a1", 1_795_000_000.5, "KKKKKKK😂")]


def test_ignora_o_que_nao_e_mensagem():
    assert yt.parse_line(json.dumps({"replayChatItemAction": {"actions": [{"addLiveChatTickerItemAction": {}}]}})) == []
    assert yt.parse_line("lixo{") == []
    assert yt.parse_line("") == []
    assert yt.parse_line(json.dumps("texto solto")) == []            # linha que não é objeto não derruba o leitor
    assert yt.parse_line(json.dumps([1, 2])) == []


def test_le_o_arquivo_do_chat_conforme_cresce_sem_repetir(tmp_path):
    tail = yt.FragmentTail(tmp_path)
    (tmp_path / "chat.live_chat.json.part-Frag3").write_text('{\n "resposta": "crua do youtube"\n}', encoding="utf-8")
    main = tmp_path / "chat.live_chat.json.part"
    main.write_text(_line("a", "oi", 1_000_000) + "\n" + _line("b", "KKK", 2_000_000)[:20], encoding="utf-8")
    assert [t for _, t in tail.poll()] == ["oi"]                     # linha pela metade espera completar
    main.write_text(_line("a", "oi", 1_000_000) + "\n" + _line("b", "KKK", 2_000_000) + "\n", encoding="utf-8")
    assert [t for _, t in tail.poll()] == ["KKK"]
    assert tail.poll() == []                                          # o que já leu não volta
    final = tmp_path / "chat.live_chat.json"                          # a live acabou: yt-dlp renomeia
    main.rename(final)
    with final.open("a", encoding="utf-8") as f:
        f.write(_line("c", "WTF", 3_000_000) + "\n")
    assert [t for _, t in tail.poll()] == ["WTF"]

def test_leitura_nao_trava_o_arquivo(tmp_path):
    f = tmp_path / "chat.live_chat.json.part-Frag3"
    f.write_text(_line("a", "oi", 1_000_000) + "\n", encoding="utf-8")
    assert yt.read_shared(f).startswith(b"{")
    f.rename(tmp_path / "renomeado")                                     # nada ficou preso


def test_id_do_video_ao_vivo(monkeypatch):
    class P:
        stdout = "hOOiQagTyTA\tis_live\n"
    monkeypatch.setattr(yt.subprocess, "run", lambda *a, **k: P())
    assert yt.live_video_id("https://www.youtube.com/channel/UCdlJx-w37viXFNq__qZ7ARQ/live") == "hOOiQagTyTA"

    class Q:
        stdout = "abc\twas_live\n"
    monkeypatch.setattr(yt.subprocess, "run", lambda *a, **k: Q())
    assert yt.live_video_id("https://www.youtube.com/@x/live") is None
