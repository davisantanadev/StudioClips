import pytest

from clipador import db
from clipador.config import APP_DIR, Settings, load_config, save_webcam


def test_load_config_has_12_streamers():
    settings, streamers = load_config(APP_DIR / "streamers.yaml")
    assert len(streamers) >= 20
    assert settings.max_clip_s == 90 and settings.ideal_clip_s == 60
    names = {s.name for s in streamers}
    assert {"alanzoka", "kaicenat", "ishowspeed", "dona", "yuri22", "loudcoringa"} <= names
    kai = next(s for s in streamers if s.name == "kaicenat")
    assert {c.platform for c in kai.channels} == {"youtube", "twitch"}
    assert not kai.is_pt
    assert "#cortesdelive" in settings.base_hashtags


def test_rejects_clip_over_90s():
    assert Settings(max_clip_s=90).max_clip_s == 90   # Shorts/TikTok aceitam mais de 1 min
    with pytest.raises(ValueError):
        Settings(max_clip_s=91)
    with pytest.raises(ValueError):
        Settings(ideal_clip_s=95)                     # ideal não passa do máximo


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


def test_url_do_youtube_por_handle_e_por_id():
    from clipador.config import Channel
    assert Channel("youtube", "@Gameplayrj").url == "https://www.youtube.com/@Gameplayrj/live"
    assert Channel("youtube", "piuzinho").url == "https://www.youtube.com/@piuzinho/live"
    assert Channel("youtube", "UCJAbkIY17zAmCTNQHH0bQoQ").url == \
        "https://www.youtube.com/channel/UCJAbkIY17zAmCTNQHH0bQoQ/live"


def test_lista_padrao_tem_os_canais_certos():
    from clipador.config import DEFAULTS_DIR
    _, streamers = load_config(DEFAULTS_DIR / "streamers.yaml")
    by = {s.name: {(c.platform, c.channel) for c in s.channels} for s in streamers}
    assert len(by) == len(streamers)                                   # sem nome repetido
    assert ("youtube", "UCJAbkIY17zAmCTNQHH0bQoQ") in by["davyjones"]  # Davy Jones faz live no GameplayRJ
    assert ("twitch", "paulinholokobr") in by["paulinhooloko"]
    assert ("twitch", "piuzinho") in by["piuzinho"]
    assert ("kick", "coringa") in by["loudcoringa"]
    assert ("twitch", "loud_coringa") not in by["loudcoringa"]          # esse canal não existe
    assert "eveleone2004" not in by
    assert len(streamers) >= 20

def test_so_brasileiros_mais_kai_cenat_e_ishowspeed():
    from clipador.config import DEFAULTS_DIR
    _, streamers = load_config(DEFAULTS_DIR / "streamers.yaml")
    gringos = {s.name for s in streamers if s.lang != "pt"}
    assert gringos == {"kaicenat", "ishowspeed"}
    settings, _ = load_config(DEFAULTS_DIR / "streamers.yaml")
    assert settings.discover_langs == ["pt"]             # achados sozinho: só live em português

def test_por_padrao_so_clipa_quem_esta_na_lista():
    from clipador.config import DEFAULTS_DIR
    settings, streamers = load_config(DEFAULTS_DIR / "streamers.yaml")
    assert settings.auto_discover is False
    luan = next(s for s in streamers if s.name == "luanz7")
    assert ("kick", "luanz7") in {(c.platform, c.channel) for c in luan.channels} and luan.is_pt

def test_banco_antigo_ganha_as_colunas_novas_sem_perder_clipes(tmp_path):
    """Banco de versão antiga (sem Lixeira e sem YouTube) abre e mantém os clipes."""
    import sqlite3
    from clipador import db
    p = tmp_path / "antigo.db"
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, streamer TEXT NOT NULL,
        display TEXT NOT NULL, platform TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, workdir TEXT NOT NULL,
        video_path TEXT NOT NULL, cover_path TEXT NOT NULL, card TEXT NOT NULL, title TEXT NOT NULL,
        description TEXT NOT NULL, hashtags TEXT NOT NULL, transcript TEXT NOT NULL DEFAULT '',
        lang TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pronto',
        posted INTEGER NOT NULL DEFAULT 0)""")
    c.execute("INSERT INTO clips (streamer, display, created_at, workdir, video_path, cover_path, card, title, "
              "description, hashtags) VALUES ('a','A',1,'w','w/f.mp4','w/c.jpg','[]','t','d','[]')")
    c.commit()
    c.close()
    db.init(p)
    got = db.list_clips()
    assert len(got) == 1 and got[0].yt_status == "" and got[0].deleted_at == 0
    db.init(p)                                   # abrir de novo não quebra