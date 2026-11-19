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
    assert c.post("/api/clips/bulk-delete", json={"ids": [a, b]}).json()["deleted"] == 2
    assert db.list_clips() == []


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