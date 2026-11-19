import subprocess
from pathlib import Path

import pytest

from clipador.cutter import clip_window, cut_clip
from clipador.recorder import Segment, ffmpeg_bin
from tests.helpers import duration, make_video


def _segs(n, dur=10.0, t0=1000.0, paths=None):
    return [Segment(paths[i] if paths else Path(f"s{i}.ts"), t0 + i * dur, dur) for i in range(n)]


def test_window_default():
    segs = _segs(18)
    assert clip_window(segs, 1100, 40, 15, 60) == (1060, 1115)


def test_window_capped_at_60():
    s, e = clip_window(_segs(18), 1100, 70, 20, 60)
    assert e - s == 60 and e == 1100 + 20


def test_window_peak_at_start_of_buffer():
    s, e = clip_window(_segs(6), 1005, 40, 15, 60)
    assert s == 1000 and e == 1055


def test_window_peak_at_end_of_buffer():
    s, e = clip_window(_segs(6), 1058, 40, 15, 60)
    assert e == 1060 and s == 1005


@pytest.mark.slow
def test_cut_real_segments(tmp_path):
    src = make_video(tmp_path / "src.mp4", 60)
    subprocess.run([ffmpeg_bin(), "-loglevel", "error", "-i", str(src), "-c", "copy", "-f", "segment",
                    "-segment_time", "10", "-reset_timestamps", "1", str(tmp_path / "seg_%03d.ts")], check=True)
    paths = sorted(tmp_path.glob("seg_*.ts"))
    segs = _segs(len(paths), paths=paths)
    out, dur, peak_off = cut_clip(segs, 1050, 40, 15, 60, tmp_path / "clip.mp4")
    assert dur == pytest.approx(55) and peak_off == pytest.approx(45)
    assert duration(out) == pytest.approx(dur, abs=1.2)
