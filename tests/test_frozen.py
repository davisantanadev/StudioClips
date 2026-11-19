"""O .exe congelado precisa rodar `-m streamlink`/`-m yt_dlp` e achar o FFmpeg que vem junto."""
import json
import os
import sys

from clipador import frozen


def _fake_module(tmp_path, monkeypatch):
    (tmp_path / "modfalso.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv))\nif '--falha' in sys.argv: sys.exit(3)\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))  # o despacho troca sys.argv; o monkeypatch desfaz


def test_roda_modulo_pedido_com_menos_m(tmp_path, capsys, monkeypatch):
    _fake_module(tmp_path, monkeypatch)
    assert frozen.run_module_if_requested(["StudioClips.exe", "-m", "modfalso", "--stdout", "url"]) is True
    argv = json.loads(capsys.readouterr().out)
    assert argv[0].endswith("modfalso.py") and argv[1:] == ["--stdout", "url"]  # igual ao python -m


def test_sem_menos_m_nao_faz_nada():
    assert frozen.run_module_if_requested(["StudioClips.exe", "abrir"]) is False
    assert frozen.run_module_if_requested(["StudioClips.exe"]) is False
    assert frozen.run_module_if_requested(["StudioClips.exe", "-m"]) is False


def test_modulo_que_sai_com_codigo_vira_systemexit(tmp_path, monkeypatch):
    import pytest
    _fake_module(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as e:
        frozen.run_module_if_requested(["StudioClips.exe", "-m", "modfalso", "--falha"])
    assert e.value.code == 3


def test_ffmpeg_embutido_entra_no_comeco_do_path(tmp_path, monkeypatch):
    pasta = tmp_path / "Meus Apps" / "São Clips"
    (pasta / "ffmpeg").mkdir(parents=True)
    (pasta / "ffmpeg" / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", "C:\\outro")
    assert frozen.add_bundled_ffmpeg_to_path(pasta) is True
    assert os.environ["PATH"].split(os.pathsep)[0] == str(pasta / "ffmpeg")
    assert frozen.add_bundled_ffmpeg_to_path(pasta) is True  # de novo: não duplica
    assert os.environ["PATH"].split(os.pathsep).count(str(pasta / "ffmpeg")) == 1


def test_sem_ffmpeg_embutido_nao_mexe_no_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "C:\\outro")
    assert frozen.add_bundled_ffmpeg_to_path(tmp_path) is False
    assert os.environ["PATH"] == "C:\\outro"


def test_caminhos_congelados(tmp_path, monkeypatch):
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "StudioClips.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe_dir / "_internal"), raising=False)
    app_dir, res_dir = frozen.app_dirs()
    assert app_dir == exe_dir
    assert res_dir == exe_dir / "_internal"


def test_caminhos_pelo_codigo_fonte(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    app_dir, res_dir = frozen.app_dirs()
    assert app_dir == res_dir
    assert (app_dir / "clipador" / "frozen.py").exists()
