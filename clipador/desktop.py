"""Janela própria do painel (StudioClips.exe): usa o WebView2 que já vem no Windows, sem abrir o navegador.

Uma janela só com o painel pesa bem menos que uma aba do navegador (sem extensões, abas e processos
extras). Fechar a janela desliga o Clipador. Sem pywebview/WebView2, quem chama volta pro navegador.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
_window = None  # a janela aberta (pra abrir o seletor de pasta)
TITLE = "StudioClips — Clipador GTA 6"
APP_ID = "StudioClips.Clipador"


def icon_path():
    from .config import RES_DIR
    p = RES_DIR / "assets" / "studioclips.ico"
    return p if p.exists() else None


def set_taskbar_identity() -> bool:
    """Sem isto, rodando pelo pythonw (INICIAR.bat) a barra de tarefas agrupa a janela com o Python e mostra
    o ícone dele. Com um AppUserModelID próprio, ela mostra o ícone da janela (o do StudioClips)."""
    import sys
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID) == 0
    except Exception:
        return False


def available() -> bool:
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def open_window(url: str, storage_dir=None) -> bool:
    """Abre o painel numa janela e só volta quando ela é fechada. False se não deu pra abrir a janela."""
    try:
        import webview
    except Exception:
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True                  # botões ⬇ Vídeo / Capa / zip
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True   # "criar chave grátis" abre no navegador
        global _window
        _window = webview.create_window(TITLE, url, width=1360, height=880, min_size=(900, 600),
                                        background_color="#0B0B0F")
        set_taskbar_identity()
        icon = icon_path()
        webview.start(private_mode=False, storage_path=str(storage_dir) if storage_dir else None,
                      icon=str(icon) if icon else None)
        return True
    except Exception:
        log.exception("não deu pra abrir a janela do painel — usando o navegador")
        return False


def pick_folder(start: str | None = None) -> str | None:
    """Abre o seletor de pasta do Windows na janela do app. None se não tiver janela (modo navegador) ou cancelar."""
    if _window is None:
        return None
    try:
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        got = _window.create_file_dialog(kind, directory=start or "")
    except Exception:
        log.exception("seletor de pasta")
        return None
    if not got:
        return None
    return str(got[0] if isinstance(got, (list, tuple)) else got)
