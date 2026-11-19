"""Achar sozinho as lives de GTA mais vistas (Twitch, Kick, YouTube) e reconhecer o dono de uma VOD pelo link."""
from clipador import discover as dc
from clipador.config import Channel, Streamer, load_config, save_extra_streamer

FIXOS = [
    Streamer("alanzoka", "Alanzoka", "pt", [Channel("twitch", "alanzoka"), Channel("youtube", "@alanzoka")]),
    Streamer("davyjones", "Davy Jones", "pt", [Channel("youtube", "UCJAbkIY17zAmCTNQHH0bQoQ"),
                                                Channel("twitch", "davyjones")]),
    Streamer("gaules", "Gaules", "pt", [Channel("twitch", "gaules")]),
]


def F(platform, channel, display, viewers, lang="", title="GTA 6 ao vivo", handle=""):
    return dc.Found(platform, channel, display, viewers, lang, title, handle)


# ---------- descoberta ----------
def test_junta_so_quem_nao_esta_na_lista_e_ordena_por_audiencia():
    found = [F("twitch", "Alanzoka", "alanzoka", 85000, "pt"),            # já está na lista (canal)
             F("youtube", "UC5ZTRH1zclthyc6b_D3m2Pw", "Gaules", 7700),       # já está (mesmo nome, outra plataforma)
             F("twitch", "ohnepixel", "ohnepixel", 49000, "en"),
             F("youtube", "UCHDV3JN5aqxAy9hwTTVHJow", "Skorpion Gamer", 13700, "pt"),
             F("kick", "luken", "luken", 5400, "es"),
             F("kick", "pequeno", "pequeno", 900, "pt")]                       # abaixo do mínimo
    auto = dc.merge(FIXOS, found, min_viewers=3000, max_n=10)
    assert [s.display for s in auto] == ["ohnepixel", "Skorpion Gamer", "luken"]
    assert all(s.auto and s.enabled for s in auto)
    sk = auto[1]
    assert sk.channels == [Channel("youtube", "UCHDV3JN5aqxAy9hwTTVHJow")] and sk.lang == "pt"
    assert sk.name == dc.auto_name("youtube", "UCHDV3JN5aqxAy9hwTTVHJow") and sk.name.startswith("auto-youtube-")


def test_limite_de_achados():
    found = [F("twitch", f"c{i}", f"C{i}", 10000 + i, "en") for i in range(20)]
    auto = dc.merge(FIXOS, found, min_viewers=3000, max_n=5)
    assert len(auto) == 5 and auto[0].display == "C19"


def test_mesma_pessoa_em_duas_plataformas_entra_uma_vez():
    found = [F("twitch", "ohnepixel", "ohnepixel", 49000, "en"),
             F("youtube", "UC3ti8PjJczdDKHSwk7TESkw", "ohnepixel", 7100, "en")]
    auto = dc.merge(FIXOS, found, min_viewers=3000, max_n=10)
    assert len(auto) == 1 and auto[0].channels[0].platform == "twitch"


def test_quem_o_usuario_desmarcou_fica_de_fora():
    found = [F("twitch", "ohnepixel", "ohnepixel", 49000, "en")]
    auto = dc.merge(FIXOS, found, min_viewers=3000, max_n=10, disabled={dc.auto_name("twitch", "ohnepixel")})
    assert auto == []


def test_idioma_pelo_titulo_quando_a_plataforma_nao_diz():
    assert dc.guess_lang("🔴 GTA 6 AO VIVO! A AVENTURA CONTINUA") == "pt"
    assert dc.guess_lang("GTA 6 ATÉ ZERAR!!!") == "pt"
    assert dc.guess_lang("GTA 6 | DÍA 2 MODO HISTORIA EN DIRECTO") == "es"
    assert dc.guess_lang("PLAYING GTA 6 FOR THE FIRST TIME") == "en"
    assert dc.guess_lang("GTA VI  เล่นอีกคืน") == ""            # não sei: o Whisper descobre sozinho


def test_parse_twitch_kick_youtube():
    tw = {"data": {"game": {"streams": {"edges": [
        {"node": {"viewersCount": 85680, "title": "VICE CITY", "broadcaster": {
            "login": "alanzoka", "displayName": "alanzoka", "broadcastSettings": {"language": "PT"}}}}]}}}}
    assert dc.parse_twitch(tw) == [F("twitch", "alanzoka", "alanzoka", 85680, "pt", "VICE CITY")]
    kk = {"data": [{"viewer_count": 24040, "session_title": "GTA VI MODO HISTORIA", "language": "Portuguese",
                    "channel": {"slug": "gabepeixe", "user": {"username": "Gabepeixe"}}}]}
    assert dc.parse_kick(kk) == [F("kick", "gabepeixe", "Gabepeixe", 24040, "pt", "GTA VI MODO HISTORIA")]
    yt = {"entries": [{"channel_id": "UCvCfpQXRXdJdL07pzTIA6Cw", "channel": "Kai Cenat Live", "live_status": "is_live",
                       "concurrent_view_count": 44751, "title": "PLAYING GTA 6 FOR THE FIRST TIME"},
                      {"channel_id": "UCx", "channel": "Velho", "live_status": "was_live",
                       "concurrent_view_count": 0, "title": "GTA 6"},
                      {"channel_id": "UCy", "channel": "Outro jogo", "live_status": "is_live",
                       "concurrent_view_count": 9000, "title": "Minecraft"}]}
    got = dc.parse_youtube(yt)
    assert [g.display for g in got] == ["Kai Cenat Live"] and got[0].lang == "en" and got[0].viewers == 44751


def test_uma_plataforma_fora_do_ar_nao_derruba_as_outras(monkeypatch):
    def quebra(*a, **k):
        raise OSError("sem rede")
    monkeypatch.setattr(dc, "twitch_top", quebra)
    monkeypatch.setattr(dc, "kick_top", lambda limit=30: [F("kick", "luken", "luken", 5400, "es")])
    monkeypatch.setattr(dc, "youtube_top", lambda limit=20: [])
    assert [f.channel for f in dc.discover_all()] == ["luken"]


# ---------- VOD: de quem é o link ----------
def test_link_da_vod_por_plataforma():
    assert dc.vod_ref("https://www.twitch.tv/videos/2581234567") == ("twitch", "2581234567")
    assert dc.vod_ref("https://kick.com/gabepeixe/videos/0b6a5c1e-1111-2222-3333-444455556666") == \
        ("kick", "gabepeixe")
    assert dc.vod_ref("https://www.youtube.com/watch?v=abc123XYZ_-") == ("youtube", "abc123XYZ_-")
    assert dc.vod_ref("https://youtu.be/abc123XYZ_-") == ("youtube", "abc123XYZ_-")
    assert dc.vod_ref("https://www.youtube.com/live/abc123XYZ_-?si=x") == ("youtube", "abc123XYZ_-")
    assert dc.vod_ref("https://exemplo.com/video") is None


def test_acha_o_streamer_da_lista_pelo_canal_ou_pelo_nome():
    assert dc.match_streamer(F("twitch", "Alanzoka", "alanzoka", 0), FIXOS).name == "alanzoka"
    assert dc.match_streamer(F("youtube", "UCJAbkIY17zAmCTNQHH0bQoQ", "Gameplayrj", 0), FIXOS).name == "davyjones"
    assert dc.match_streamer(F("youtube", "UCIwspRtKNszHhIhl36gREjQ", "alanzoka", 0, handle="@alanzoka"),
                             FIXOS).name == "alanzoka"
    assert dc.match_streamer(F("kick", "gaules", "Gaules", 0), FIXOS).name == "gaules"   # mesmo nome
    assert dc.match_streamer(F("twitch", "ninguem", "Ninguém", 0), FIXOS) is None


def test_streamer_novo_da_vod_fica_salvo_na_lista(tmp_path):
    novo = dc.to_streamer(F("twitch", "ohnepixel", "ohnepixel", 0, "en"))
    assert novo.name == "ohnepixel" and not novo.auto
    save_extra_streamer(novo)
    cfg = tmp_path / "s.yaml"
    cfg.write_text("streamers:\n  - name: alanzoka\n    channels:\n      - {platform: twitch, channel: alanzoka}\n",
                   encoding="utf-8")
    _, streamers = load_config(cfg)
    assert {s.name for s in streamers} == {"alanzoka", "ohnepixel"}
    save_extra_streamer(novo)                                    # de novo: não duplica
    assert [s.name for s in load_config(cfg)[1]].count("ohnepixel") == 1


def test_canal_com_sufixo_live_e_a_mesma_pessoa():
    fixos = FIXOS + [Streamer("kaicenat", "Kai Cenat", "en", [Channel("twitch", "kaicenat")])]
    found = [F("youtube", "UCvCfpQXRXdJdL07pzTIA6Cw", "Kai Cenat Live", 44751, "en"),
             F("kick", "gaulesoficial", "GaulesOficial", 9000, "pt"),
             F("kick", "eray", "Eray", 9000, "tr")]
    auto = dc.merge(fixos, found, min_viewers=3000, max_n=10)
    assert [s.display for s in auto] == ["Eray"]


def test_so_os_idiomas_escolhidos():
    found = [F("kick", "eray", "Eray", 30000, "tr"), F("kick", "luken", "luken", 5400, "es"),
             F("youtube", "UCq", "Sem idioma", 4000, "")]
    auto = dc.merge(FIXOS, found, min_viewers=3000, max_n=10, langs={"pt", "en", "es"})
    assert [s.display for s in auto] == ["luken"]                # idioma desconhecido fica de fora

def test_idioma_desconhecido_fica_de_fora_quando_filtra():
    found = [F("youtube", "UCq", "Sem idioma", 4000, ""), F("youtube", "UCb", "Br", 4000, "pt")]
    assert [s.display for s in dc.merge(FIXOS, found, 3000, 10, langs={"pt"})] == ["Br"]