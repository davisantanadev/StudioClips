import random

from clipador.detector import HypeDetector, classify


def _simulate(det, start, secs, rate, text="oi", fires=None):
    """Alimenta `rate` msgs/s por `secs` segundos e checa a cada segundo."""
    fires = [] if fires is None else fires
    rnd = random.Random(42)
    for s in range(int(secs)):
        t0 = start + s
        n = max(0, int(rnd.gauss(rate, rate ** 0.5 if rate else 0)))
        for i in range(n):
            det.feed_chat(t0 + i / max(n, 1), text)
        r = det.check(t0 + 1)
        if r is not None:
            fires.append(r)
    return fires


def test_calm_chat_never_fires():
    det = HypeDetector(cooldown_s=120)
    assert _simulate(det, 0, 600, 5) == []


def test_burst_fires_once_with_cooldown():
    det = HypeDetector(cooldown_s=120)
    fires = _simulate(det, 0, 300, 5)
    fires = _simulate(det, 300, 12, 40, "KEKW KEKW", fires)
    assert len(fires) == 1
    assert 295 <= fires[0] <= 312
    assert det.last_signals["kind"] == "risada"
    fires = _simulate(det, 312, 40, 5, fires=fires)
    fires = _simulate(det, 352, 12, 40, "KEKW", fires)  # dentro do cooldown
    assert len(fires) == 1
    fires = _simulate(det, 364, 120, 5, fires=fires)
    fires = _simulate(det, 484, 12, 45, "OMG", fires)  # depois do cooldown
    assert len(fires) == 2
    assert det.last_signals["kind"] == "susto/choque"


def test_tiny_chat_needs_min_rate():
    det = HypeDetector(cooldown_s=120)
    fires = _simulate(det, 0, 300, 0.1)
    fires = _simulate(det, 300, 10, 1, fires=fires)  # 10x a média, mas 1 msg/s
    assert fires == []


def test_sub_train_and_neutral_spam_do_not_fire():
    det = HypeDetector(cooldown_s=120)
    fires = _simulate(det, 0, 300, 5)
    fires = _simulate(det, 300, 15, 40, "obrigado pelo sub!", fires)   # enxurrada de sub
    fires = _simulate(det, 315, 15, 40, "!drops", fires)                # comando de bot
    fires = _simulate(det, 330, 15, 40, "boa tarde", fires)             # chat rápido mas sem reação
    assert fires == []


def test_scream_plus_chat_fires():
    det = HypeDetector(cooldown_s=120)
    for s in range(300):  # áudio normal
        det.feed_audio(s, -30.0)
    fires = _simulate(det, 0, 300, 5)
    for s in range(300, 304):  # grito
        det.feed_audio(s, -9.0)
    fires = _simulate(det, 300, 10, 22, "nossa", fires)
    assert len(fires) == 1 and det.last_signals["kind"] == "grito"


def test_classify():
    assert classify("KKKKKKKK") == "laugh" and classify("kekw") == "laugh" and classify("😂😂") == "laugh"
    assert classify("WTF???") == "shock" and classify("socorro que medo") == "shock"
    assert classify("valeu pelo sub") == "noise" and classify("!loco") == "noise"
    assert classify("boa noite") == "neutral"
