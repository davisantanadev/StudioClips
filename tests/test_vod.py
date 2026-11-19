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
