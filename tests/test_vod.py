import random

from clipador.vod import chat_scores, pick_moments, platform_of


def test_vod_finds_bursts_and_spaces_them():
    rnd = random.Random(1)
    msgs = [(rnd.uniform(0, 3600), "oi") for _ in range(3600 * 4)]  # 4 msg/s base
    for center in (600, 1800, 1830, 3000):  # 1800 e 1830 são o mesmo momento
        msgs += [(center + rnd.uniform(0, 8), "KEKW KEKW") for _ in range(400)]
    moments = pick_moments(chat_scores(msgs, 3600), top_n=6)
    ts = [m.t for m in moments]
    assert len(ts) == 3
    for want, got in zip((600, 1800, 3000), ts):
        assert abs(got - want) < 20


def test_calm_vod_has_no_moments():
    rnd = random.Random(2)
    msgs = [(rnd.uniform(0, 1800), "oi") for _ in range(1800 * 4)]
    assert pick_moments(chat_scores(msgs, 1800), top_n=5) == []


def test_platform_of():
    assert platform_of("https://www.twitch.tv/videos/123") == "twitch"
    assert platform_of("https://youtu.be/abc") == "youtube"


def test_audio_progress_reads_ytdlp_and_ffmpeg_lines(tmp_path, monkeypatch):
    """Twitch: o yt-dlp passa pro ffmpeg, que só diz 'time='. Os dois formatos viram porcentagem."""
    import subprocess
    from clipador import vod

    lines = ["[download]  10.0% of 50MiB\n", "size=1024KiB time=01:00:00.00 bitrate=1\n",
             "size=2048KiB time=02:00:00.00 bitrate=1\n"]

    class P:
        stdout = iter(lines)
        pid = 1

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(vod.subprocess, "Popen", lambda *a, **k: P())
    monkeypatch.setattr(vod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    seen = []
    vod.audio_scores("u", tmp_path, 4 * 3600, progress=seen.append)
    assert seen == [0.1, 0.25, 0.5]


# ---------- ideias do gtavi-claudecode (MIT): replay do chat do YouTube pelo yt-dlp e "Mais repetidos" ----------
import json as _json

from clipador import vod as _vod


def _replay_line(offset_ms, text, mid="x"):
    return _json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(offset_ms), "actions": [
        {"addChatItemAction": {"item": {"liveChatTextMessageRenderer": {
            "id": mid, "message": {"runs": [{"text": text}]}, "timestampUsec": "1795000000000000"}}}}]}})


def test_le_o_replay_do_chat_do_youtube_pelo_tempo_da_vod():
    assert _vod.parse_replay_line(_replay_line(83_500, "KKKKKKK")) == [(83.5, "KKKKKKK")]
    assert _vod.parse_replay_line("lixo") == []
    assert _vod.parse_replay_line(_json.dumps({"replayChatItemAction": {"actions": []}})) == []


def test_replay_do_youtube_usa_o_ytdlp(tmp_path, monkeypatch):
    calls = []

    class FakeProc:
        def __init__(self, cmd, **kw):
            calls.append(cmd)
            out = tmp_path / "vodchat"
            (out / "chat.live_chat.json").write_text(
                _replay_line(1000, "oi", "a") + "\n" + _replay_line(2000, "WTF", "b") + "\n", encoding="utf-8")
            self.returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(_vod.subprocess, "Popen", FakeProc)
    msgs = _vod.chat_replay("https://www.youtube.com/watch?v=czLjal_UA9Q", workdir=tmp_path / "vodchat")
    assert msgs == [(1.0, "oi"), (2.0, "WTF")]
    assert "live_chat" in calls[0] and "yt_dlp" in " ".join(calls[0])


def test_mais_repetidos_sobe_os_trechos_que_a_galera_reviu():
    scores = [0.0] * 600                                  # 20 min em baldes de 2 s
    heat = [{"start_time": 0, "end_time": 60, "value": 0.1}, {"start_time": 300, "end_time": 312, "value": 1.0},
            {"start_time": 600, "end_time": 1200, "value": 0.1}]
    out = _vod.heatmap_boost(scores, heat, bucket=2.0)
    assert out[150] >= 3.5 and out[155] >= 3.5           # 300–312 s: o pico de "mais repetidos"
    assert out[10] < 1 and out[400] < 1                   # o resto quase não muda
    assert scores[150] == 0.0                             # não mexe na lista original
    assert _vod.heatmap_boost(scores, [], bucket=2.0) == scores