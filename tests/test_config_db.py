import pytest

from clipador import db
from clipador.config import APP_DIR, Settings, load_config, save_webcam


def test_load_config_has_12_streamers():
    settings, streamers = load_config(APP_DIR / "streamers.yaml")
    assert len(streamers) == 15
    assert settings.max_clip_s == 60
    names = {s.name for s in streamers}
    assert {"alanzoka", "xqc", "montanablack88", "dona", "yuri22", "loudcoringa"} <= names
    xqc = next(s for s in streamers if s.name == "xqc")
    assert {c.platform for c in xqc.channels} == {"kick", "twitch"}
    assert not xqc.is_pt
    assert "#cortesdelive" in settings.base_hashtags


def test_rejects_clip_over_60s():
    with pytest.raises(ValueError):
        Settings(max_clip_s=61)


def test_webcam_override(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"settings:\n  data_dir: {tmp_path.as_posix()}\nstreamers:\n"
                   "  - {name: a, lang: pt, channels: [{platform: twitch, channel: a}]}\n")
    s, _ = load_config(cfg)
    save_webcam(s, "a", (10, 20, 300, 200))
    _, st = load_config(cfg)
    assert st[0].webcam == (10, 20, 300, 200)


def test_db_roundtrip(tmp_path):
    db.init(tmp_path / "c.db")
    cid = db.add_clip(db.Clip(streamer="a", display="A", workdir="w", video_path="v.mp4",
                              cover_path="c.jpg", card=["linha 1", "linha 2"], title="t",
                              description="d", hashtags=["#GTA6"]))
    assert db.list_clips(posted=False)[0].card == ["linha 1", "linha 2"]
    db.mark_posted(cid)
    assert db.list_clips(posted=False) == []
    assert db.get_clip(cid).posted is True
