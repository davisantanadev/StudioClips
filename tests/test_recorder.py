import time

import pytest

from clipador.recorder import Recorder, mean_volume_db
from tests.helpers import fake_live_cmd

pytestmark = pytest.mark.slow


def test_records_segments_and_prunes(tmp_path):
    got = []
    rec = Recorder("fake", tmp_path / "buf", segment_s=2, keep=3,
                   source_cmd=fake_live_cmd(14), on_segment=got.append)
    rec.start()
    deadline = time.time() + 40
    while rec.is_alive() and time.time() < deadline:
        time.sleep(0.5)
    time.sleep(1)
    rec.stop()
    segs = rec.segments()
    assert len(got) >= 5
    assert len(segs) == 3
    assert all(s.path.exists() for s in segs)
    assert len(list((tmp_path / "buf").glob("seg_*.ts"))) == 3
    # tempos contínuos e crescentes
    for a, b in zip(segs, segs[1:]):
        assert abs(b.start_ts - a.end_ts) < 0.5
    assert mean_volume_db(segs[-1].path) is not None


def test_source_dies_cleanly(tmp_path):
    rec = Recorder("fake", tmp_path / "buf", segment_s=2, source_cmd=fake_live_cmd(3))
    rec.start()
    deadline = time.time() + 20
    while rec.is_alive() and time.time() < deadline:
        time.sleep(0.3)
    assert not rec.is_alive()
    rec.stop()  # não levanta exceção
