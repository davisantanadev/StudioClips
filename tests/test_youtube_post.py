"""Postar no YouTube pelo Composio: agora (público) ou agendado (o próprio YouTube publica no horário)."""
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from clipador import db, youtube as yt
from clipador.config import Settings
from clipador.web.app import create_app


class FakeComposio:
    """Imita o composio.exe: guarda os comandos e responde como ele."""

    def __init__(self, upload_ok=True):
        self.calls, self.bodies = [], []
        self.upload_ok = upload_ok

    def __call__(self, args, timeout=0):
        self.calls.append(list(args))
        for i, a in enumerate(args):
            if a == "-d" and args[i + 1].startswith("@"):
                self.bodies.append(json.loads(open(args[i + 1][1:], encoding="utf-8").read()))
        out = {"successful": True, "data": {}}
        code = 0
        if args[0] == "execute" and args[1] == "YOUTUBE_MULTIPART_UPLOAD_VIDEO":
            if self.upload_ok:
                out["data"] = {"response_data": {"id": "dQw4w9WgXcQ", "status": {"uploadStatus": "uploaded"}}}
            else:
                out, code = {"successful": False, "error": "quota exceeded"}, 1
        if args[0] == "connections":
            out = {"youtube": [{"id": "ca_1", "status": "ACTIVE", "word_id": "youtube_x"}]}
        if args[0] == "whoami":
            out = {"email": "x@y.com"}
        return subprocess.CompletedProcess(args, code, json.dumps(out), "")


def test_titulo_tags_e_descricao_dentro_dos_limites_do_youtube():
    assert len(yt.clean_title("a" * 300)) <= 100 and "<" not in yt.clean_title("<b>oi</b>")
    tags = yt.clean_tags(["#GTA6", "#Alanzoka", "#fail"] + [f"#tag{i}" for i in range(200)])
    assert tags[:3] == ["GTA6", "Alanzoka", "fail"] and len(",".join(tags)) <= 450
    desc = yt.build_description("Mano, olha isso 💀", ["#GTA6", "#cortes"])
    assert desc.startswith("Mano, olha isso 💀") and desc.rstrip().endswith("#GTA6 #cortes")
    assert len(yt.build_description("é" * 4000, []).encode("utf-8")) <= 4900


def test_acha_o_id_do_video_em_qualquer_formato_de_resposta():
    assert yt.find_video_id({"data": {"response_data": {"id": "dQw4w9WgXcQ"}}}) == "dQw4w9WgXcQ"
    assert yt.find_video_id({"data": {"videoId": "abcdefghijk"}}) == "abcdefghijk"
    assert yt.find_video_id({"data": {"id": "123"}}) is None


def test_postar_agora_sobe_publico_na_categoria_jogos(tmp_path, monkeypatch):
    fake = FakeComposio()
    monkeypatch.setattr(yt, "_run", fake)
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    vid = yt.upload(video, "Título", "Desc", ["GTA6"], "public")
    assert vid == "dQw4w9WgXcQ"
    cmd = fake.calls[0]
    assert cmd[:2] == ["execute", "YOUTUBE_MULTIPART_UPLOAD_VIDEO"] and str(video) in cmd
    body = fake.bodies[0]
    assert body["privacyStatus"] == "public" and body["categoryId"] == "20" and body["tags"] == ["GTA6"]


def test_agendar_usa_o_publishat_do_proprio_youtube(monkeypatch):
    fake = FakeComposio()
    monkeypatch.setattr(yt, "_run", fake)
    when = datetime(2026, 11, 21, 18, 30, tzinfo=timezone.utc)
    yt.schedule("dQw4w9WgXcQ", when)
    cmd = fake.calls[0]
    assert cmd[0] == "proxy" and "part=status" in cmd[1] and "PUT" in cmd
    assert fake.bodies[0] == {"id": "dQw4w9WgXcQ", "status": {"privacyStatus": "private",
                                                             "publishAt": "2026-11-21T18:30:00Z",
                                                             "selfDeclaredMadeForKids": False}}


def test_erro_do_composio_vira_mensagem_clara(tmp_path, monkeypatch):
    monkeypatch.setattr(yt, "_run", FakeComposio(upload_ok=False))
    (tmp_path / "v.mp4").write_bytes(b"x")
    try:
        yt.upload(tmp_path / "v.mp4", "t", "d", [], "public")
        assert False, "devia falhar"
    except yt.ComposioError as e:
        assert "limite diário" in str(e)          # "quota exceeded" vira aviso em português


def _app(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(yt, "_run", fake)
    monkeypatch.setattr(yt, "composio_bin", lambda: "composio.exe")
    s = Settings(data_dir=tmp_path)
    db.init(tmp_path / "c.db")
    w = tmp_path / "clips" / "x"
    w.mkdir(parents=True)
    (w / "final.mp4").write_bytes(b"video")
    cid = db.add_clip(db.Clip(streamer="alanzoka", display="Alanzoka", workdir="clips/x",
                              video_path="clips/x/final.mp4", cover_path="clips/x/capa.jpg", card=["a"],
                              title="Que fuga 😂", description="Mano.", hashtags=["#GTA6"]))
    return TestClient(create_app(s)), cid


def _wait_status(c, cid, want):
    for _ in range(100):
        clip = next(x for x in c.get("/api/clips").json() if x["id"] == cid)
        if clip["yt_status"] in want:
            return clip
        time.sleep(0.05)
    raise AssertionError(clip)


def test_painel_postar_agora_marca_publicado_e_postado(tmp_path, monkeypatch):
    c, cid = _app(tmp_path, monkeypatch, FakeComposio())
    assert c.get("/api/youtube/status").json()["connected"] is True
    r = c.post(f"/api/clips/{cid}/youtube", json={"title": "Que fuga 😂", "description": "Mano.",
                                                   "hashtags": ["#GTA6"], "mode": "now"})
    assert r.status_code == 200
    clip = _wait_status(c, cid, {"publicado"})
    assert clip["yt_video_id"] == "dQw4w9WgXcQ" and clip["posted"] is True
    assert clip["title"] == "Que fuga 😂"                       # o texto editado fica salvo no clipe


def test_painel_agendar_no_futuro_e_recusar_horario_passado(tmp_path, monkeypatch):
    fake = FakeComposio()
    c, cid = _app(tmp_path, monkeypatch, fake)
    passado = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
    assert c.post(f"/api/clips/{cid}/youtube", json={"title": "t", "description": "d", "hashtags": [],
                                                      "mode": "schedule", "when": passado}).status_code == 400
    futuro = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert c.post(f"/api/clips/{cid}/youtube", json={"title": "t", "description": "d", "hashtags": [],
                                                      "mode": "schedule", "when": futuro}).status_code == 200
    clip = _wait_status(c, cid, {"agendado"})
    assert clip["yt_when"] > time.time()
    assert fake.bodies[0]["privacyStatus"] == "private"                           # sobe privado…
    assert fake.bodies[1]["status"]["privacyStatus"] == "private"                 # …e o YouTube publica na hora


def test_sem_youtube_conectado_avisa(tmp_path, monkeypatch):
    class Desconectado(FakeComposio):
        def __call__(self, args, timeout=0):
            if args[0] == "connections":
                return subprocess.CompletedProcess(args, 0, json.dumps({"youtube": [{"status": "INITIALIZING"}]}), "")
            return super().__call__(args, timeout)
    c, cid = _app(tmp_path, monkeypatch, Desconectado())
    assert c.get("/api/youtube/status").json()["connected"] is False
    r = c.post(f"/api/clips/{cid}/youtube", json={"title": "t", "description": "d", "hashtags": [], "mode": "now"})
    assert r.status_code == 409 and "Conectar YouTube" in r.json()["detail"]


def test_cota_diaria_do_youtube_vira_aviso_claro(tmp_path, monkeypatch):
    """Erro real de 19/11/2026: o projeto do Google do Composio (compartilhado) estourou o limite diário."""
    quota = {"successful": False, "error": json.dumps({"error": {"code": 429, "message": "Quota exceeded for quota "
             "metric 'Video Uploads' and limit 'Video Uploads per day' of service 'youtube.googleapis.com'",
             "status": "RESOURCE_EXHAUSTED"}})}
    monkeypatch.setattr(yt, "_run", lambda args, timeout=0: subprocess.CompletedProcess(args, 1, json.dumps(quota), ""))
    (tmp_path / "v.mp4").write_bytes(b"x")
    try:
        yt.upload(tmp_path / "v.mp4", "t", "d", [], "public")
        assert False
    except yt.ComposioError as e:
        msg = str(e)
        assert "limite diário" in msg and "Composio" in msg and len(msg) < 400