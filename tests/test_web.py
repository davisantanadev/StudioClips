from fastapi.testclient import TestClient

from clipador import db
from clipador.config import APP_DIR, Settings, load_webcams
from clipador.web.app import create_app


def _client(tmp_path):
    s = Settings(data_dir=tmp_path)
    db.init(tmp_path / "c.db")
    return s, TestClient(create_app(s))


def _add():
    return db.add_clip(db.Clip(streamer="alanzoka", display="Alanzoka", workdir="w", video_path="w/final.mp4",
                               cover_path="w/capa.jpg", card=["a"], title="t", description="d",
                               hashtags=["#GTA6"]))


def test_list_and_mark_posted(tmp_path):
    _, c = _client(tmp_path)
    cid = _add()
    assert c.get("/api/clips?posted=false").json()[0]["id"] == cid
    assert c.post(f"/api/clips/{cid}/posted", json={"posted": True}).status_code == 200
    assert c.get("/api/clips?posted=false").json() == []


def test_edit_texts(tmp_path):
    _, c = _client(tmp_path)
    cid = _add()
    r = c.patch(f"/api/clips/{cid}", json={"title": "novo", "hashtags": ["#GTA6", "#x"]}).json()
    assert r["title"] == "novo" and r["hashtags"] == ["#GTA6", "#x"]


def test_save_webcam_persists(tmp_path, monkeypatch):
    s, c = _client(tmp_path)
    monkeypatch.setattr("clipador.web.app.load_config", lambda: (s, __import__("clipador.config", fromlist=["x"]).load_config(APP_DIR / "streamers.yaml")[1]))
    r = c.put("/api/streamers/alanzoka/webcam", json={"rect": [900, 500, 600, 400]})
    assert r.status_code == 200
    assert load_webcams(s)["alanzoka"] == [900, 500, 380, 220]  # recortado pra caber em 1280x720
    assert c.put("/api/streamers/naoexiste/webcam", json={"rect": None}).status_code == 404


def test_status_without_runner(tmp_path):
    _, c = _client(tmp_path)
    j = c.get("/api/status").json()
    assert j["running"] is False and j["live"] == []
    assert c.get("/").status_code == 200 and c.get("/webcam").status_code == 200


def test_bulk_zip_posted_delete(tmp_path):
    import io, zipfile
    _, c = _client(tmp_path)
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "final.mp4").write_bytes(b"vid")
    (tmp_path / "w" / "capa.jpg").write_bytes(b"img")
    a, b = _add(), _add()
    r = c.post("/api/clips/zip", json={"ids": [a, b]})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert f"Alanzoka_{a}/Alanzoka_{a}.mp4" in names and f"Alanzoka_{b}/post.txt" in names
    c.post("/api/clips/bulk-posted", json={"ids": [a], "posted": True})
    assert db.get_clip(a).posted and not db.get_clip(b).posted
    assert c.post("/api/clips/bulk-delete", json={"ids": [a, b]}).json()["trashed"] == 2
    assert db.list_clips() == [] and len(db.list_clips(trash=True)) == 2      # foram pra Lixeira
    assert (tmp_path / "w" / "final.mp4").exists()


def test_settings_saved_outside_app_and_applied(tmp_path):
    from clipador.config import load_user_settings, user_dir
    s, c = _client(tmp_path)
    r = c.get("/api/settings").json()
    assert r["groq_key_set"] is False
    r = c.put("/api/settings", json={"handle": "@davi", "min_score": 8, "groq_key": "gsk_abcdef123456"}).json()
    assert r["handle"] == "@davi" and r["groq_key_set"] and "gsk_abcdef123456" not in str(r)
    assert s.handle == "@davi" and s.min_score == 8          # vale na hora, sem reiniciar
    assert load_user_settings()["handle"] == "@davi"
    assert "gsk_abcdef123456" in (user_dir() / ".env").read_text(encoding="utf-8")
    assert c.put("/api/settings", json={"min_score": 99}).status_code == 422


def test_chaves_da_twitch_no_painel(tmp_path, monkeypatch):
    import os
    from clipador.config import user_dir
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    _, c = _client(tmp_path)
    assert c.get("/api/settings").json()["twitch"]["set"] is False
    r = c.put("/api/settings", json={"twitch_client_id": "abcd1234efgh5678", "twitch_client_secret": "s3cr3tvalue99"}).json()
    assert r["twitch"]["set"] is True
    assert "s3cr3tvalue99" not in str(r)                 # o segredo nunca volta pro painel
    env = (user_dir() / ".env").read_text(encoding="utf-8")
    assert "TWITCH_CLIENT_ID=abcd1234efgh5678" in env and "TWITCH_CLIENT_SECRET=s3cr3tvalue99" in env
    assert os.environ["TWITCH_CLIENT_SECRET"] == "s3cr3tvalue99"


def test_testar_twitch_sem_chave(tmp_path, monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    _, c = _client(tmp_path)
    assert c.post("/api/settings/test-twitch").status_code == 400

def test_qualidade_maxima(tmp_path):
    s, c = _client(tmp_path)
    r = c.put("/api/settings", json={"quality": "max"}).json()
    assert r["quality"] == "max" and s.quality == "best"
    assert c.put("/api/settings", json={"quality": "hd"}).json()["quality"] == "hd"

def _fake_identify(monkeypatch, found):
    from clipador import discover as dc
    monkeypatch.setattr(dc, "identify_vod", lambda url: found)
    return dc


def test_identifica_o_dono_da_vod_que_esta_na_lista(tmp_path, monkeypatch):
    dc = _fake_identify(monkeypatch, None)
    dc_found = dc.Found("youtube", "UCJAbkIY17zAmCTNQHH0bQoQ", "Gameplayrj", 0, "pt")
    monkeypatch.setattr(dc, "identify_vod", lambda url: dc_found)
    _, c = _client(tmp_path)
    r = c.get("/api/vod/identify", params={"url": "https://www.youtube.com/watch?v=abc123XYZ_-"}).json()
    assert r["name"] == "davyjones" and r["display"] == "Davy Jones" and r["new"] is False


def test_vod_de_quem_nao_esta_na_lista_entra_sozinho(tmp_path, monkeypatch):
    dc = _fake_identify(monkeypatch, None)
    monkeypatch.setattr(dc, "identify_vod", lambda url: dc.Found("twitch", "fulanogamer", "FulanoGamer", 0, "pt"))
    s, c = _client(tmp_path)
    r = c.get("/api/vod/identify", params={"url": "https://www.twitch.tv/videos/1"}).json()
    assert r["new"] is True and r["display"] == "FulanoGamer" and r["platform"] == "twitch"
    created = []
    monkeypatch.setattr(type(c.app.state.vods), "create", lambda self, url, st, n=6: created.append(st) or "t1")
    assert c.post("/api/vod", json={"url": "https://www.twitch.tv/videos/1", "top_n": 3}).status_code == 200
    assert created == ["fulanogamer"]
    assert any(x["name"] == "fulanogamer" for x in c.get("/api/streamers").json())   # agora está na lista


def test_vod_sem_dono_reconhecido_pede_pra_escolher(tmp_path, monkeypatch):
    _fake_identify(monkeypatch, None)
    _, c = _client(tmp_path)
    assert c.get("/api/vod/identify", params={"url": "https://exemplo.com/x"}).json()["found"] is False
    r = c.post("/api/vod", json={"url": "https://exemplo.com/x"})
    assert r.status_code == 400 and "escolha" in r.json()["detail"]


def test_ligar_e_desligar_descoberta_no_painel(tmp_path):
    s, c = _client(tmp_path)
    assert c.get("/api/settings").json()["auto_discover"] is False      # padrão: só quem está na lista
    r = c.put("/api/settings", json={"auto_discover": True, "discover_min_viewers": 10000}).json()
    assert r["auto_discover"] is True and r["discover_min_viewers"] == 10000 and s.auto_discover is True

def test_link_do_canal_vira_canal():
    from clipador.discover import parse_channel_url as p
    from clipador.config import Channel
    assert p("https://www.twitch.tv/LuanZ7") == Channel("twitch", "luanz7")
    assert p("twitch.tv/piuzinho?sr=a") == Channel("twitch", "piuzinho")
    assert p("https://kick.com/gabepeixe") == Channel("kick", "gabepeixe")
    assert p("https://www.youtube.com/@PaulinhoLOKOoficial/live") == Channel("youtube", "@PaulinhoLOKOoficial")
    assert p("https://youtube.com/channel/UCdlJx-w37viXFNq__qZ7ARQ") == Channel("youtube", "UCdlJx-w37viXFNq__qZ7ARQ")
    assert p("@luanz7") == Channel("youtube", "@luanz7")
    assert p("https://www.twitch.tv/videos/123") is None and p("oi") is None


def test_adicionar_streamer_pelo_painel(tmp_path):
    from clipador.config import extra_streamers_path
    _, c = _client(tmp_path)
    r = c.post("/api/streamers", json={"display": "Fulano Gamer",
                                       "links": "https://www.twitch.tv/fulanogamer https://kick.com/fulanogamer"})
    assert r.status_code == 200 and r.json()["name"] == "fulanogamer"
    got = {x["name"]: x for x in c.get("/api/streamers").json()}
    assert {ch["platform"] for ch in got["fulanogamer"]["channels"]} == {"twitch", "kick"}
    assert got["fulanogamer"]["extra"] is True and got["alanzoka"]["extra"] is False
    assert extra_streamers_path().exists()                                  # salvo fora da pasta do app
    assert c.post("/api/streamers", json={"display": "X", "links": "nada"}).status_code == 400
    assert c.delete("/api/streamers/fulanogamer").status_code == 200        # quem você pôs, você tira
    assert "fulanogamer" not in {x["name"] for x in c.get("/api/streamers").json()}
    assert c.delete("/api/streamers/alanzoka").status_code == 400           # os da lista padrão não