"""Clipe nunca some sozinho: excluir manda pra Lixeira; só "Apagar de vez" (na Lixeira) remove os arquivos."""
import json
import time

from fastapi.testclient import TestClient

from clipador import db
from clipador.config import Settings
from clipador.recover import recover_orphans
from clipador.web.app import create_app


def _clip_dir(root, name):
    d = root / "clips" / name
    d.mkdir(parents=True)
    (d / "final.mp4").write_bytes(b"video")
    (d / "capa.jpg").write_bytes(b"jpg")
    return d


def _setup(tmp_path):
    s = Settings(data_dir=tmp_path)
    db.init(tmp_path / "c.db")
    d = _clip_dir(tmp_path, "20261119-140102_alanzoka")
    cid = db.add_clip(db.Clip(streamer="alanzoka", display="Alanzoka", workdir="clips/20261119-140102_alanzoka",
                              video_path="clips/20261119-140102_alanzoka/final.mp4",
                              cover_path="clips/20261119-140102_alanzoka/capa.jpg", card=["a"], title="t",
                              description="d", hashtags=[]))
    return s, TestClient(create_app(s)), cid, d


def test_excluir_manda_pra_lixeira_sem_apagar_arquivo(tmp_path):
    s, c, cid, d = _setup(tmp_path)
    assert c.delete(f"/api/clips/{cid}").status_code == 200
    assert c.get("/api/clips").json() == []                              # sumiu da lista normal
    assert [x["id"] for x in c.get("/api/clips?trash=true").json()] == [cid]
    assert (d / "final.mp4").exists()                                    # o vídeo continua lá
    assert c.post(f"/api/clips/{cid}/restore").status_code == 200
    assert [x["id"] for x in c.get("/api/clips").json()] == [cid]


def test_excluir_varios_tambem_vai_pra_lixeira(tmp_path):
    s, c, cid, d = _setup(tmp_path)
    assert c.post("/api/clips/bulk-delete", json={"ids": [cid]}).json()["trashed"] == 1
    assert (d / "final.mp4").exists() and c.get("/api/clips").json() == []
    assert c.post("/api/clips/bulk-restore", json={"ids": [cid]}).status_code == 200
    assert len(c.get("/api/clips").json()) == 1


def test_apagar_de_vez_so_vale_pra_quem_esta_na_lixeira(tmp_path):
    s, c, cid, d = _setup(tmp_path)
    assert c.post("/api/clips/bulk-purge", json={"ids": [cid]}).json()["purged"] == 0   # não está na lixeira
    assert (d / "final.mp4").exists()
    c.delete(f"/api/clips/{cid}")
    assert c.post("/api/trash/empty").json()["purged"] == 1
    assert not (d / "final.mp4").exists() and c.get("/api/clips?trash=true").json() == []


def test_recupera_clipe_que_ficou_no_disco_mas_sumiu_da_lista(tmp_path):
    db.init(tmp_path / "c.db")
    d = _clip_dir(tmp_path, "20261118-112626_alanzoka")
    (d / "selecao.json").write_text(json.dumps({
        "card": ["Alanzoka reclama da mira", "e tenta arrombar porta"], "title": "Mira ruim 🚪",
        "description": "A porta infantil.", "hashtags": ["#Fail"], "by_ai": True}), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"streamer": "alanzoka", "platform": "twitch"}), encoding="utf-8")
    (d / "transcript.json").write_text(json.dumps({"pt": {"lang": "pt", "segments": [
        {"start": 0, "end": 1, "text": "mano", "words": []}]}}), encoding="utf-8")
    sem_video = tmp_path / "clips" / "20261118-120000_cellbit"          # momento descartado: sem final.mp4
    sem_video.mkdir()
    apagado = _clip_dir(tmp_path, "20261118-130000_dona")               # apagado de vez, mas a pasta ficou presa
    (apagado / ".apagado").write_text("x", encoding="utf-8")

    n = recover_orphans(tmp_path, {"alanzoka": "Alanzoka"})
    assert n == 1
    trash = db.list_clips(trash=True)
    assert len(trash) == 1 and db.list_clips() == []                    # volta pra Lixeira, não pra fila
    r = trash[0]
    assert r.display == "Alanzoka" and r.title == "Mira ruim 🚪" and r.card[0] == "Alanzoka reclama da mira"
    assert r.platform == "twitch" and r.transcript == "mano"
    assert time.localtime(r.created_at).tm_mday == 18
    assert recover_orphans(tmp_path, {}) == 0                           # rodar de novo não duplica


def test_clipe_de_achado_sozinho_volta_com_o_nome_da_lista(tmp_path):
    db.init(tmp_path / "c.db")
    d = _clip_dir(tmp_path, "20261119-150703_autokickluanz7")
    (d / "meta.json").write_text(json.dumps({"streamer": "auto-kick-luanz7", "platform": "kick"}), encoding="utf-8")
    assert recover_orphans(tmp_path, {"luanz7": "LuanZ7"}) == 1
    c = db.list_clips(trash=True)[0]
    assert c.streamer == "luanz7" and c.display == "LuanZ7"