"""Salvar vídeo e capa direto na pasta escolhida (sem a janela "Salvar como", que salvava sem .mp4)."""
from fastapi.testclient import TestClient

from clipador import db, desktop
from clipador.config import Settings
from clipador.export import export_name
from clipador.web.app import create_app


def _setup(tmp_path, title="QUE PORRA FOI ESSA? 💀"):
    s = Settings(data_dir=tmp_path / "data")
    db.init(tmp_path / "c.db")
    w = tmp_path / "data" / "clips" / "x"
    w.mkdir(parents=True)
    (w / "final.mp4").write_bytes(b"video")
    (w / "capa.jpg").write_bytes(b"jpg")
    cid = db.add_clip(db.Clip(streamer="alanzoka", display="Alanzoka", workdir="clips/x",
                              video_path="clips/x/final.mp4", cover_path="clips/x/capa.jpg", card=["a"],
                              title=title, description="d", hashtags=[]))
    return s, TestClient(create_app(s)), cid


def test_nome_do_arquivo_vem_do_titulo_e_e_valido_no_windows():
    assert export_name("Alanzoka", 'QUE PORRA FOI ESSA? 💀', 7) == "Alanzoka - QUE PORRA FOI ESSA"
    assert export_name("Cellbit", 'a/b:c*d"e<f>g|h', 3) == "Cellbit - a b c d e f g h"
    assert export_name("Dona", "GTA 6. o fim...", 9) == "Dona - GTA 6. o fim"     # sem ponto no fim
    assert export_name("Dona", "", 9) == "Dona - clipe 9"
    assert len(export_name("X", "a" * 300, 1)) <= 90


def test_salva_video_e_capa_direto_nas_pastas_escolhidas(tmp_path):
    s, c, cid = _setup(tmp_path)
    vids, caps = tmp_path / "meus videos", tmp_path / "minhas capas"
    r = c.put("/api/settings", json={"save_video_dir": str(vids), "save_cover_dir": str(caps)}).json()
    assert r["save_video_dir"] == str(vids) and r["save_cover_dir"] == str(caps)
    got = c.post(f"/api/clips/{cid}/export", json={"kind": "video"}).json()
    assert got["paths"] == [str(vids / "Alanzoka - QUE PORRA FOI ESSA.mp4")]
    assert (vids / "Alanzoka - QUE PORRA FOI ESSA.mp4").read_bytes() == b"video"   # é o vídeo, com .mp4
    got = c.post(f"/api/clips/{cid}/export", json={"kind": "cover"}).json()
    assert got["paths"] == [str(caps / "Alanzoka - QUE PORRA FOI ESSA.jpg")]
    again = c.post(f"/api/clips/{cid}/export", json={"kind": "video"}).json()        # não sobrescreve
    assert again["paths"] == [str(vids / "Alanzoka - QUE PORRA FOI ESSA (2).mp4")]


def test_salvar_varios_de_uma_vez(tmp_path):
    s, c, cid = _setup(tmp_path)
    c.put("/api/settings", json={"save_video_dir": str(tmp_path / "v"), "save_cover_dir": str(tmp_path / "c")})
    got = c.post("/api/clips/bulk-export", json={"ids": [cid]}).json()
    assert sorted(p.rsplit(".", 1)[1] for p in got["paths"]) == ["jpg", "mp4"]


def test_pastas_padrao_quando_nao_escolheu(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    s, c, cid = _setup(tmp_path)
    r = c.get("/api/settings").json()
    assert r["save_video_dir"].endswith("StudioClips") and "Videos" in r["save_video_dir"]
    assert "Pictures" in r["save_cover_dir"]


def test_pasta_invalida_e_recusada(tmp_path):
    s, c, cid = _setup(tmp_path)
    assert c.put("/api/settings", json={"save_video_dir": "pasta/relativa"}).status_code == 400


def test_escolher_pasta_pela_janela_do_app(tmp_path, monkeypatch):
    s, c, cid = _setup(tmp_path)
    monkeypatch.setattr(desktop, "pick_folder", lambda start=None: None)               # sem janela do app
    assert c.post("/api/pick-folder", json={"kind": "video"}).status_code == 409
    monkeypatch.setattr(desktop, "pick_folder", lambda start=None: str(tmp_path / "escolhida"))
    assert c.post("/api/pick-folder", json={"kind": "video"}).json()["path"] == str(tmp_path / "escolhida")
