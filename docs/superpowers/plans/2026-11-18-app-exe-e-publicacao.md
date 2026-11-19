# App .exe e publicação — plano

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** empacotar o Clipador como `StudioClips.exe` (janela nativa) e publicar no GitHub sem segredos.
**Architecture:** caminhos cientes de "congelado" em `config.py`; despacho `-m` no ponto de entrada; janela
pywebview em `clipador/desktop.py`; spec PyInstaller + workflow de Release.
**Tech Stack:** Python 3.12 (CI), PyInstaller 6, pywebview 5+, FFmpeg (gyan.dev essentials), GitHub Actions.
**Spec:** `docs/superpowers/specs/2026-11-18-app-exe-e-publicacao-design.md`

## Global Constraints
- Nada de `.env`, `data/`, chaves reais ou logs no Git. Arquivos ≥ 100 MB nunca no Git.
- Comportamento pelo código-fonte (`INICIAR.bat`, `python -m clipador ...`) não muda.

## Review Focus
- .exe aberto de pasta com espaço/acento no caminho → `data/` e `streamers.yaml` criados ao lado do .exe.
- Filho `StudioClips.exe -m streamlink --stdout` com stdout em pipe → bytes chegam no ffmpeg.
- Janela fechada → runner para e processo termina (sem ffmpeg órfão).
- Modo navegador com aba em segundo plano → não desliga sozinho (sinal de vida ≤ 60 s < IDLE_S 150 s).
- Máquina sem WebView2 → cai no navegador em vez de quebrar.

### Task 1: caminhos e despacho para o .exe
**Files:** `clipador/config.py`, `clipador/render.py`, `clipador/webcam.py`, `clipador/__main__.py`,
`clipador/frozen.py` (novo), `tests/test_frozen.py` (novo)
- Produces: `config.RES_DIR: Path`, `config.FROZEN: bool`; `frozen.run_module_if_requested(argv) -> bool`;
  `frozen.add_bundled_ffmpeg_to_path(app_dir) -> bool`.
- [ ] Testes: `run_module_if_requested(["x","-m","json.tool", arquivo])` roda o módulo e retorna True;
  `["x","abrir"]` retorna False; `add_bundled_ffmpeg_to_path` com `ffmpeg/ffmpeg.exe` fake põe a pasta no
  início do PATH; sem a pasta retorna False e não mexe.
- [ ] Implementar; fontes/modelo via `RES_DIR`; rodar suíte rápida (97 + novos passando).

### Task 2: janela nativa e painel leve
**Files:** `clipador/desktop.py` (novo), `clipador/__main__.py`, `clipador/web/static/app.js`, `style.css`
- [ ] `desktop.open_window(url, title) -> bool` (False se pywebview/WebView2 indisponível); `abrir` usa
  janela quando disponível, sem Presence; ao fechar: `runner.stop()` + `server.should_exit`.
- [ ] JS: com `document.hidden`, pular `loadClips`/`loadVods` e chamar `loadStatus` no máx. a cada 30 s;
  `visibilitychange` recarrega. CSS: topo sem `backdrop-filter`; `.dot` pausado com `html.hidden`.

### Task 3: build e release
**Files:** `packaging/studioclips.spec`, `packaging/entry.py`, `packaging/build.ps1`,
`.github/workflows/release.yml`, `.gitignore`, `README.md`
- [ ] Build local; testar `StudioClips.exe` (janela abre, painel responde, `-m streamlink --version` e
  `-m yt_dlp --version` funcionam, fechar encerra tudo).
- [ ] Workflow: testes → build → zip → Release na tag `v*`.

### Task 4: revisão, commit, push e tag `v0.1.0`
