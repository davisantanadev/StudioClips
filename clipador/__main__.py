"""Linha de comando:
    pythonw -m clipador abrir              atalho: sem janela; fechar a aba do painel desliga tudo
    python -m clipador run                 grava as lives + painel (janela preta, não fecha com a aba)
    python -m clipador painel              só o painel, sem gravar
    python -m clipador vod <link> -s alanzoka [-n 6]
    python -m clipador teste <video> -s alanzoka [--pico 20]
    python -m clipador status              quem está ao vivo agora
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from . import db
from .ai import any_key
from .config import APP_DIR, load_config, load_env


def _setup():
    load_env()
    for stream in (sys.stdout, sys.stderr):  # emojis no console do Windows
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    settings, streamers = load_config()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stderr),
                                  logging.FileHandler(settings.data_dir / "clipador.log", encoding="utf-8")])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("streamlink").setLevel(logging.ERROR)
    db.init(settings.data_dir / "clipes.db")
    try:  # clipe que ficou no disco mas saiu da lista volta pra Lixeira (nunca some sem você decidir)
        from .recover import recover_orphans
        recover_orphans(settings.data_dir, {s.name: s.display for s in streamers})
    except Exception:
        logging.exception("recuperação de clipes")
    if not any_key("text"):
        logging.warning("⚠ sem chave do Groq — cole no painel (Ferramentas → Configurações). "
                        "Sem ela a IA não escolhe os cortes e o card sai genérico")
    if settings.handle in ("", "@seuperfil"):
        logging.warning("⚠ coloque o seu @ no painel (Ferramentas → Configurações)")
    return settings, streamers


def _serve(settings, runner, open_browser=True, close_with_tab=False, window=False):
    """window=True: painel numa janela própria (fechar a janela desliga tudo). Se a janela não abrir,
    cai no navegador com "fechar a aba desliga"."""
    import time

    import uvicorn
    from .web.app import create_app
    from .web.presence import Presence

    url = f"http://localhost:{settings.port}"
    logging.info("🌐 painel em %s", url)
    if open_browser and not window:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    presence = Presence() if (close_with_tab or window) else None
    server = uvicorn.Server(uvicorn.Config(create_app(settings, runner, presence=presence), host="127.0.0.1",
                                           port=settings.port, log_level="warning",
                                           log_config=None if sys.stderr is None else uvicorn.config.LOGGING_CONFIG))

    def watchdog():
        while not server.should_exit:
            why = presence.reason_to_exit()
            if why:
                logging.info("⏻ %s — fechando o Clipador", why)
                if runner:
                    runner.stop()
                server.should_exit = True
                return
            time.sleep(2)

    if window:
        from . import desktop
        from .config import user_dir
        t = threading.Thread(target=server.run, daemon=True, name="painel")
        t.start()
        for _ in range(150):  # espera o painel subir (até 15 s)
            if server.started or not t.is_alive():
                break
            time.sleep(0.1)
        if t.is_alive() and desktop.open_window(url, user_dir() / "janela"):
            logging.info("⏻ janela fechada — fechando o Clipador")
            if runner:
                runner.stop()
            server.should_exit = True
            t.join(15)
            return
        if t.is_alive():  # sem WebView2: navegador, e fechar a aba desliga
            webbrowser.open(url)
            threading.Thread(target=watchdog, daemon=True, name="aba").start()
        t.join()
        return
    if presence is not None:
        threading.Thread(target=watchdog, daemon=True, name="aba").start()
    server.run()


def _already_running(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list[str] | None = None) -> None:
    # sem console (pythonw, atalho da Área de Trabalho): print/log não podem quebrar
    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    p = argparse.ArgumentParser(prog="clipador")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("abrir", help="abre o painel (liga o Clipador se precisar); fechar a aba desliga tudo")
    r = sub.add_parser("run")
    r.add_argument("--sem-navegador", action="store_true")
    pn = sub.add_parser("painel")
    pn.add_argument("--sem-navegador", action="store_true")
    pn.add_argument("--porta", type=int, default=None, help="porta do painel (padrão: a das configurações)")
    sub.add_parser("status")
    v = sub.add_parser("vod")
    v.add_argument("url")
    v.add_argument("-s", "--streamer", required=True)
    v.add_argument("-n", "--quantos", type=int, default=6)
    t = sub.add_parser("teste")
    t.add_argument("video", type=Path)
    t.add_argument("-s", "--streamer", required=True)
    t.add_argument("--pico", type=float, default=20.0, help="segundo do momento bom no vídeo")
    a = p.parse_args(argv)
    cmd = a.cmd or "run"
    if cmd == "abrir":
        from .config import load_config as _lc
        port = _lc()[0].port
        if _already_running(port):  # já está ligado: só abre o painel de novo
            from . import desktop
            if not desktop.open_window(f"http://localhost:{port}"):
                webbrowser.open(f"http://localhost:{port}")
            return
    settings, streamers = _setup()
    by_name = {s.name: s for s in streamers}

    if cmd in ("run", "abrir"):
        from .runner import Runner
        from .winjob import kill_children_on_exit
        kill_children_on_exit()  # gravações e ffmpeg morrem junto com o Clipador
        runner = Runner(settings, streamers)
        runner.start()
        runner.note(f"👀 vigiando {sum(s.enabled for s in streamers)} streamers")
        try:
            from . import desktop
            _serve(settings, runner, not getattr(a, "sem_navegador", False), close_with_tab=cmd == "abrir",
                   window=cmd == "abrir" and desktop.available())
        finally:
            runner.stop()
    elif cmd == "painel":
        if a.porta:
            settings.port = a.porta
        _serve(settings, None, not a.sem_navegador)
    elif cmd == "status":
        from .recorder import probe
        for s in streamers:
            info = next((i for i in (probe(c) for c in s.channels) if i), None)
            state = f"🔴 AO VIVO ({info.channel.platform}) {'[GTA]' if info.is_gta else ''} {info.title}" if info else "offline"
            print(f"{s.display:16} {state}")
    elif cmd == "vod":
        from .vod import clip_vod
        s = by_name.get(a.streamer) or sys.exit(f"streamer '{a.streamer}' não está no streamers.yaml")
        n = clip_vod(a.url, s, settings, a.quantos)
        print(f"{n} clipes prontos — abra o painel para baixar")
    elif cmd == "teste":
        from .cutter import cut_clip
        from .pipeline import new_workdir, process_candidate
        from .recorder import Segment
        from .render import probe_duration
        s = by_name.get(a.streamer) or sys.exit(f"streamer '{a.streamer}' não está no streamers.yaml")
        wd = new_workdir(settings, s)
        b, af = settings.analysis_before_s, settings.analysis_after_s
        src, _, off = cut_clip([Segment(a.video.resolve(), 0.0, probe_duration(a.video))], a.pico,
                               b, af, b + af, wd / "analysis.mp4")
        clip = process_candidate(s, src, off, settings, platform="teste", workdir=wd, manual=True, note=print)
        print(f"✅ {settings.data_dir / clip.video_path}\n   card: {' / '.join(clip.card)}\n   título: {clip.title}")


if __name__ == "__main__":
    main()
