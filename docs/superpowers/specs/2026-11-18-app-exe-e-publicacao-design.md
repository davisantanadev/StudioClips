# App .exe e publicação no GitHub — design

**Pedido:** subir o projeto para `github.com/davisantanadev/StudioClips` sem credenciais, de um jeito que
qualquer pessoa consiga usar, e transformar em um app `.exe` para não pesar a CPU no navegador.

## Objetivos
- Repositório público sem segredos: fica de fora `.env`, `data/` (clipes, log, banco), `.venv`, builds.
- `StudioClips.exe`: abre o painel numa janela nativa (pywebview + WebView2), sem console. Fechar a
  janela desliga gravações e edições.
- Painel mais leve: sem `backdrop-filter` no topo, a bolinha "ao vivo" e as consultas param quando a
  janela/aba está escondida (mantendo um sinal de vida lento para o modo navegador não desligar).
- Release automático: GitHub Actions (Windows, Python 3.12) roda os testes, monta o .exe com
  PyInstaller (pasta), junta o FFmpeg e publica um `.zip` em Releases ao subir uma tag `v*`.

## Não objetivos
- Instalador MSI/assinatura de código; atualização automática; mudar a lógica de clipes.
- Whisper local dentro do .exe (fica só para quem roda pelo código-fonte: +300 MB).

## Abordagens
1. **pywebview + PyInstaller pasta (escolhida):** reaproveita o painel web inteiro; WebView2 já vem no
   Windows 11; pasta inicia rápido e o antivírus implica menos que onefile.
2. Electron/Tauri: reescreve a casca e exige outro runtime/toolchain — desproporcional.
3. Só um atalho .exe que abre o navegador: não resolve o peso da aba.

## Decisões
- `APP_DIR` (arquivos do usuário: `streamers.yaml`, `hashtags.txt`, `.env`, `data/`) = pasta do .exe
  quando congelado; recursos embutidos (`assets/`) vêm de `sys._MEIPASS`.
- `sys.executable -m streamlink|yt_dlp` continua igual: o .exe congelado reconhece `-m <módulo>` e roda
  o módulo (sem abrir janela).
- FFmpeg/FFprobe embutidos em `ffmpeg/` ao lado do .exe; essa pasta entra no começo do `PATH` ao abrir.
- Sem pywebview/WebView2 disponível, `abrir` volta ao comportamento atual (navegador + fechar aba desliga).
- Configuração e chaves continuam em `%APPDATA%\ClipadorGTA6`.
