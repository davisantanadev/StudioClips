"""Clipes que os espectadores criam na Twitch marcam os melhores momentos (live e VOD)."""
from clipador import twitchclips as tc
from clipador.detector import describe

T0 = 1_800_000_000.0  # um instante qualquer (epoch)


def _clip(created, views=10, video_id="999", offset=None, dur=30.0, cid=None):
    return {"id": cid or f"c{created}{views}", "created_at": tc.to_iso(created), "view_count": views,
            "video_id": video_id, "vod_offset": offset, "duration": dur}


def test_id_da_vod_pelo_link():
    assert tc.video_id_from_url("https://www.twitch.tv/videos/2581234567") == "2581234567"
    assert tc.video_id_from_url("https://twitch.tv/videos/123?t=1h2m") == "123"
    assert tc.video_id_from_url("https://www.youtube.com/watch?v=x") is None


def test_duracao_da_twitch():
    assert tc.parse_duration("3h2m10s") == 3 * 3600 + 2 * 60 + 10
    assert tc.parse_duration("45m") == 45 * 60
    assert tc.parse_duration("") == 0


def test_iso_ida_e_volta():
    assert abs(tc.from_iso(tc.to_iso(T0)) - T0) < 1


def test_vod_so_clipes_desta_vod_com_offset():
    clips = [_clip(T0, 500, "999", offset=1000.0), _clip(T0, 5, "999", offset=None),
             _clip(T0, 50, "888", offset=200.0)]
    hits = tc.vod_hits(clips, "999")
    assert len(hits) == 1
    assert hits[0].views == 500
    assert 1000 < hits[0].t <= 1030  # o momento fica dentro do clipe, perto do fim


def test_boost_sobe_o_trecho_clipado_e_so_ele():
    scores = [0.0] * 1000
    hits = [tc.ClipHit(t=600.0, views=800), tc.ClipHit(t=604.0, views=50), tc.ClipHit(t=598.0, views=3)]
    out = tc.boost(scores, hits, bucket=2.0)
    i = int(600 // 2.0)
    assert out[i] >= 3.5           # passa do limite do VOD sozinho (3 clipes)
    assert out[100] == 0.0         # longe dali não muda
    assert scores[i] == 0.0        # não mexe na lista original


def test_um_clipe_so_ajuda_mas_nao_decide_sozinho():
    out = tc.boost([0.0] * 500, [tc.ClipHit(t=300.0, views=2)], bucket=2.0)
    assert 0 < out[150] < 3.5


def test_nota_dos_clipes_perto_do_momento():
    hits = [tc.ClipHit(600, 800), tc.ClipHit(610, 50), tc.ClipHit(900, 7)]
    assert tc.hits_near(hits, 605) == (2, 850)
    assert tc.hits_near(hits, 2000) == (0, 0)


def test_live_dois_clipes_novos_disparam_uma_vez():
    seen: set[str] = set()
    clips = [_clip(T0 - 40, cid="a"), _clip(T0 - 20, cid="b")]
    got = tc.live_burst(clips, seen, now=T0)
    assert got is not None
    moment, n, views = got
    assert n == 2 and views == 20
    assert T0 - 60 <= moment <= T0 - 25  # um pouco antes de os clipes serem criados
    assert tc.live_burst(clips, seen, now=T0 + 5) is None  # os mesmos clipes não disparam de novo


def test_live_um_clipe_ou_clipe_velho_nao_dispara():
    assert tc.live_burst([_clip(T0 - 10, cid="a")], set(), now=T0) is None
    assert tc.live_burst([_clip(T0 - 600, cid="a"), _clip(T0 - 590, cid="b")], set(), now=T0) is None


def test_sem_credenciais(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    assert tc.has_credentials() is False
    assert tc.helix() is None


class FakeHttp:
    """Imita a API da Twitch: token, usuário, vídeo e clipes (com paginação)."""

    def __init__(self):
        self.calls = []
        self.token_calls = 0

    def post(self, url, data=None, timeout=None):
        self.token_calls += 1
        return {"access_token": f"tok{self.token_calls}", "expires_in": 3600}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), headers))
        if url.endswith("/users"):
            return {"data": [{"id": "42", "login": params["login"]}]}
        if url.endswith("/videos"):
            return {"data": [{"id": params["id"], "user_id": "42", "created_at": tc.to_iso(T0),
                              "duration": "1h0m0s"}]}
        if url.endswith("/clips"):
            if params.get("after") == "p2":
                return {"data": [_clip(T0 + 100, 9, "999", offset=100.0, cid="z")], "pagination": {}}
            return {"data": [_clip(T0 + 50, 7, "999", offset=50.0, cid="y")], "pagination": {"cursor": "p2"}}
        raise AssertionError(url)


def test_helix_busca_clipes_da_vod_com_paginacao():
    http = FakeHttp()
    h = tc.Helix("cid", "secret", http=http)
    hits = h.vod_clip_hits("999")
    assert sorted(x.views for x in hits) == [7, 9]
    url, params, headers = http.calls[-1]
    assert headers["Client-Id"] == "cid" and headers["Authorization"] == "Bearer tok1"
    assert params["broadcaster_id"] == "42"
    assert http.token_calls == 1  # token reaproveitado


def test_describe_fala_dos_clipes_da_twitch():
    txt = describe({"kind": "clipado na Twitch", "twitch_clips": 3, "twitch_views": 800})
    assert "3 clipes" in txt and "Twitch" in txt


def test_vigia_ao_vivo_dispara_com_clipes_da_twitch(tmp_path, monkeypatch):
    from clipador.config import Channel, Settings, Streamer
    from clipador.recorder import LiveInfo
    from clipador.runner import StreamerWorker

    now = T0
    fake_clips = [_clip(now - 30, 4, cid="a"), _clip(now - 15, 6, cid="b")]

    class FakeHelix:
        def recent_clips(self, login):
            assert login == "alanzoka"
            return fake_clips

    monkeypatch.setattr(tc, "helix", lambda: FakeHelix())
    jobs = []
    st = Streamer("alanzoka", "Alanzoka", "pt", [Channel("twitch", "alanzoka")])
    w = StreamerWorker(st, LiveInfo(st.channels[0], "GTA 6", "GTA"), Settings(data_dir=tmp_path), jobs.append)
    assert w.poll_twitch_clips(now) is True
    assert len(jobs) == 1 and jobs[0].signals["twitch_clips"] == 2 and jobs[0].peak_ts < now - 30
    assert w.poll_twitch_clips(now + 50) is False      # mesmos clipes não disparam de novo
    monkeypatch.setattr(tc, "helix", lambda: None)
    assert w.poll_twitch_clips(now + 500) is False     # sem as chaves da Twitch: nada