"""Ponto de entrada do StudioClips.exe.

- `StudioClips.exe`            abre o painel numa janela (igual ao atalho `abrir`)
- `StudioClips.exe <comando>`  mesmos comandos do `python -m clipador` (vod, teste, status, run…)
- `StudioClips.exe -m <mod>`   uso interno: o Clipador roda streamlink/yt-dlp assim
"""
import sys

from clipador.frozen import add_bundled_ffmpeg_to_path, app_dirs, run_module_if_requested


def _use_parent_console() -> None:
    """O .exe não tem console próprio (senão abriria uma janela preta). Rodado num terminal com um comando
    (status, vod…), escreve no terminal de quem chamou. Saída redirecionada (> arquivo, pipe) já funciona."""
    if sys.stdout is not None or sys.platform != "win32":
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):  # -1 = console do processo pai
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            sys.stderr = sys.stdout
    except Exception:
        pass


add_bundled_ffmpeg_to_path(app_dirs()[0])

if __name__ == "__main__" and not run_module_if_requested(sys.argv):
    from clipador.__main__ import main

    args = sys.argv[1:] or ["abrir"]
    if args[0] != "abrir":
        _use_parent_console()
    main(args)
