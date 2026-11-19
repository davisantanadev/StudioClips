import subprocess

import pytest

from clipador.ai import Seg, Transcript, Word
from clipador.render import has_audio, make_cover, render_vertical
from clipador.subtitles import build_ass
from tests.helpers import duration, make_video

pytestmark = pytest.mark.slow


def _size(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return tuple(int(x) for x in r.stdout.strip().split(","))


def _ass(tmp_path):
    words = [Word(i * 0.5, i * 0.5 + 0.4, w) for i, w in enumerate("que isso meu deus do céu".split())]
    p = tmp_path / "w" / "subs.ass"
    p.parent.mkdir(exist_ok=True)
    p.write_text(build_ass(Transcript("pt", [Seg(0, 3, "que isso meu deus do céu", words)]), "karaoke",
                           y=1500), encoding="utf-8")
    return p


def test_render_with_webcam(tmp_path):
    src = make_video(tmp_path / "src.mp4", 8)
    out = render_vertical(src, tmp_path / "w" / "final.mp4", (900, 450, 360, 250),
                          ["Alanzoka roubou um banco", "e a polícia chegou voando"], _ass(tmp_path),
                          "@clipes", "#FF2E63")
    assert _size(out) == (1080, 1920)
    assert duration(out) == pytest.approx(8, abs=0.3)
    assert has_audio(out)
    cover = make_cover(src, 4, tmp_path / "w" / "capa.jpg", (900, 450, 360, 250), ["Card"], "@clipes")
    assert cover.exists() and _size(cover) == (1080, 1920)


def test_render_without_webcam_and_without_audio(tmp_path):
    src = make_video(tmp_path / "src.mp4", 5, audio=False)
    out = render_vertical(src, tmp_path / "w" / "final.mp4", None, ["xQc no GTA 6"], None, "", "#22D3EE")
    assert _size(out) == (1080, 1920)
    assert not has_audio(out)


def test_capa_com_tempo_depois_do_fim_nao_trava(tmp_path):
    """A IA às vezes escolhe um trecho que termina antes do pico: o tempo da capa caía depois do fim do vídeo e o
    ffmpeg ficava rodando pra sempre (travou a fila inteira em 19/11/2026)."""
    import time as _t
    (tmp_path / "w").mkdir()
    src = make_video(tmp_path / "w" / "source.mp4", 5)
    t0 = _t.time()
    cover = make_cover(src, 90, tmp_path / "w" / "capa.jpg", (900, 450, 360, 250), ["Card"], "@clipes")
    assert cover.exists() and cover.stat().st_size > 1000
    assert _t.time() - t0 < 60