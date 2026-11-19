import os

from clipador.config import (load_config, load_env, load_webcams, save_user_env, save_user_settings,
                             save_webcam, user_dir)


def test_empty_streamers_yaml_falls_back_to_defaults_and_is_restored(tmp_path):
    cfg = tmp_path / "streamers.yaml"
    cfg.write_text("", encoding="utf-8")  # o que o install.bat criava quando o arquivo faltava
    settings, streamers = load_config(cfg)
    assert len(streamers) >= 20 and {s.name for s in streamers} >= {"alanzoka", "piuzinho", "paulinhooloko"}
    assert "#cortesdelive" in settings.base_hashtags
    assert "alanzoka" in cfg.read_text(encoding="utf-8")  # arquivo recriado para o usuário editar


def test_missing_streamers_yaml_uses_defaults(tmp_path):
    _, streamers = load_config(tmp_path / "nao_existe.yaml")
    assert {s.name for s in streamers} >= {"alanzoka", "kaicenat"}


def test_user_settings_override_yaml(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text("settings:\n  handle: '@seuperfil'\n  min_score: 7\nstreamers: []\n", encoding="utf-8")
    save_user_settings({"handle": "@davi", "min_score": 8, "campo_estranho": 1})
    s, _ = load_config(cfg)
    assert s.handle == "@davi" and s.min_score == 8


def test_key_survives_an_update(tmp_path, monkeypatch):
    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / ".env").write_text("GROQ_API_KEY=gsk_teste123\n", encoding="utf-8")
    load_env(v1 / ".env")
    assert os.environ["GROQ_API_KEY"] == "gsk_teste123"
    assert "gsk_teste123" in (user_dir() / ".env").read_text(encoding="utf-8")

    # versão nova descompactada: .env vazio
    monkeypatch.delenv("GROQ_API_KEY")
    v2 = tmp_path / "v2"
    v2.mkdir()
    (v2 / ".env").write_text("", encoding="utf-8")
    load_env(v2 / ".env")
    assert os.environ["GROQ_API_KEY"] == "gsk_teste123"


def test_save_user_env_replaces_value(monkeypatch):
    save_user_env("GROQ_API_KEY", "gsk_a")
    save_user_env("GROQ_API_KEY", "gsk_b")
    txt = (user_dir() / ".env").read_text(encoding="utf-8")
    assert "gsk_b" in txt and "gsk_a" not in txt
    assert os.environ["GROQ_API_KEY"] == "gsk_b"


def test_webcams_survive_new_data_dir(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"settings:\n  data_dir: {(tmp_path / 'd1').as_posix()}\nstreamers:\n"
                   "  - {name: a, lang: pt, channels: [{platform: twitch, channel: a}]}\n")
    s, _ = load_config(cfg)
    save_webcam(s, "a", (1, 2, 300, 200))
    cfg.write_text(f"settings:\n  data_dir: {(tmp_path / 'd2').as_posix()}\nstreamers:\n"
                   "  - {name: a, lang: pt, channels: [{platform: twitch, channel: a}]}\n")
    s2, st = load_config(cfg)
    assert st[0].webcam == (1, 2, 300, 200)
    assert load_webcams(s2)["a"] == [1, 2, 300, 200]
