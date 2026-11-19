# Clipador GTA 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sistema local (Windows) que grava lives de streamers, detecta momentos de hype em tempo real e entrega clipes verticais 9:16 legendados, com card, capa, título, descrição e hashtags num painel no navegador.

**Architecture:** Um processo `clipador run` com uma thread por streamer ao vivo (gravação em buffer de segmentos + leitor de chat + detector). Ao disparar, um worker único (fila) corta → transcreve → traduz/gera textos → renderiza → grava no SQLite. Um servidor FastAPI serve o painel e o configurador de webcam.

**Tech Stack:** Python 3.11, streamlink, ffmpeg/ffprobe, faster-whisper, anthropic SDK (fallback Ollama HTTP), websockets (Kick), chat-downloader (YouTube), FastAPI + uvicorn, SQLite, PyYAML, pytest.

**Spec:** `docs/2026-11-18-clipador-gta6-design.md`

## Global Constraints
- Duração do clipe: padrão 40 s antes + 15 s depois do pico; **teto absoluto 60 s**.
- Saída 1080x1920, H.264 `-preset veryfast -crf 23`, AAC 128k.
- Layout com webcam: webcam 1080x608 (topo) · card 1080x260 (meio) · gameplay 1080x1052 (base). Sem webcam: card 1080x260 no topo, gameplay preenche o resto com fundo desfocado.
- Card: máximo 2 linhas, PT-BR, ≤ 42 caracteres por linha.
- Buffer: segmentos de 10 s, mantém os últimos 18 (3 min). Gravação em 720p (`720p,720p60,best`), `-c copy`.
- Cooldown 120 s por streamer; máximo 6 clipes/hora no total (configurável).
- BR → legenda palavra por palavra (karaokê). Não-PT → legenda traduzida PT-BR por frase.
- Segredos só em `.env` (`ANTHROPIC_API_KEY`); sem chave → Ollama `http://localhost:11434`, modelo `llama3.1`.
- Tudo deve rodar no Windows: caminhos com `pathlib`, nada de `fork`, binários achados via `shutil.which`.

## Review Focus
1. Streamer cai/termina live no meio da gravação → thread encerra limpa, sem clipe corrompido; vigia retoma quando voltar.
2. Pico nos primeiros 40 s da gravação (buffer curto) → clipe usa o que existe, nunca falha.
3. Transcrição vazia (só música/grito) → legenda omitida, textos gerados a partir do título da live; clipe sai igual.
4. LLM fora do ar/sem chave e Ollama ausente → textos de fallback por template (`"{streamer} no GTA 6 🔥"`), pipeline não para.
5. Webcam não configurada → usa layout sem webcam.

---

### Task 1: Scaffold, config e banco
**Files:** Create `pyproject.toml`, `clipador/__init__.py`, `clipador/config.py`, `clipador/db.py`, `streamers.yaml`, `.env.example`, `hashtags.txt`; Test `tests/test_config.py`, `tests/test_db.py`
**Interfaces — Produces:**
- `Streamer(name:str, platform:Literal["twitch","kick","youtube"], channel:str, lang:str, webcam:tuple[int,int,int,int]|None)` (webcam = x,y,w,h em pixels do frame 1280x720)
- `Settings(pre_s=40, post_s=15, max_clip_s=60, cooldown_s=120, max_per_hour=6, whisper_model="small", max_live=12, handle="@", accent="#FF2E63", data_dir=Path("data"))`
- `load_config(path) -> tuple[Settings, list[Streamer]]`
- `db.init(path)`, `db.add_clip(Clip) -> int`, `db.list_clips(posted:bool|None) -> list[Clip]`, `db.mark_posted(id)`; `Clip(id, streamer, created_at, video_path, cover_path, card, title, description, hashtags, transcript, posted)`
- [ ] Teste: `load_config` lê streamers.yaml com os 12 streamers; recusa `max_clip_s > 60` (ValueError).
- [ ] Teste: add/list/mark_posted round-trip em DB temporário.
- [ ] Implementar; rodar `pytest -q`; commit.
- `streamers.yaml` inicial: alanzoka, davyjones, bistecone, cellbit, blackoutz (lang pt); djmariio (es), eveleone2004 (en, canal pendente, `enabled:false`), xqc (en), buster (ru), trymacs (de), typicalgamer (en, youtube), montanablack88 (de). Plataformas verificadas na hora.

### Task 2: Buffer de gravação
**Files:** Create `clipador/recorder.py`; Test `tests/test_recorder.py`
**Interfaces — Produces:** `class Recorder(streamer, out_dir, segment_s=10, keep=18)`: `.start()`, `.stop()`, `.is_alive() -> bool`, `.segments() -> list[Segment(path, start_ts:float, dur:float)]`; `is_live(streamer) -> bool` (via `streamlink --json`).
- Approach: `streamlink -O <url> 720p,720p60,best` piped para `ffmpeg -f segment -segment_time 10 -reset_timestamps 1 -c copy seg_%06d.ts`; start_ts = wall-clock da criação; poda > keep.
- [ ] Teste: com fonte fake (`ffmpeg -f lavfi testsrc+sine` em vez do streamlink, injetada por parâmetro `source_cmd`) por 35 s → ≥3 segmentos, podado a `keep`.
- [ ] Teste (Review 1): matar a fonte → `is_alive()` False em ≤5 s, sem exceção.
- [ ] Implementar; pytest; commit.

### Task 3: Leitores de chat
**Files:** Create `clipador/chat/{__init__,twitch,kick,youtube}.py`; Test `tests/test_chat.py`
**Interfaces — Produces:** `ChatReader(streamer)` com `.start()`, `.stop()`, `.drain() -> list[ChatMsg(ts:float, text:str)]`. Twitch: IRC anônimo `justinfan12345` em `irc.chat.twitch.tv:6667`. Kick: websocket Pusher do chatroom (id via `https://kick.com/api/v2/channels/<slug>`). YouTube: `chat_downloader`.
- [ ] Teste: parser de linha IRC `PRIVMSG` → ChatMsg; parser de evento Pusher `ChatMessageEvent` → ChatMsg (fixtures em JSON).
- [ ] Implementar; leitor que falha só loga e tenta reconectar a cada 15 s; commit.

### Task 4: Detector de hype
**Files:** Create `clipador/detector.py`; Test `tests/test_detector.py`
**Interfaces — Produces:** `class HypeDetector(cooldown_s, z_threshold=3.0, min_rate=2.0)`: `.feed_chat(ts, n_msgs)`, `.feed_audio(ts, rms_db)`, `.check(now) -> float|None` (retorna ts do pico). `class GlobalLimiter(max_per_hour)`: `.allow(now) -> bool`.
- Algoritmo: taxa de chat em janelas de 2 s; média e desvio por EWMA (α=0.05) das últimas ~10 min; score = z_chat + 0.5·z_audio (audio via `ffmpeg astats` no último segmento) ; dispara se score ≥ z_threshold e taxa ≥ min_rate; pico = janela de maior score nos últimos 10 s; bloqueia por cooldown. Bônus +1 se ≥30% das msgs na janela forem emotes/padrões de hype (`KEKW|LUL|OMEGALUL|POG|\?\?\?|KKK+|WTF|OMG|LMAO`).
- [ ] Testes: base calma 5 msg/s por 5 min + surto 40 msg/s → dispara 1 vez; segundo surto dentro de 120 s → não dispara; limiter recusa a 7ª em 1 h.
- [ ] Implementar; commit.

### Task 5: Corte do buffer
**Files:** Create `clipador/cutter.py`; Test `tests/test_cutter.py`
**Interfaces — Produces:** `cut_clip(segments, peak_ts, pre_s, post_s, max_s, out:Path) -> Path` (concat demuxer + `-ss/-t`, re-mux para mp4).
- [ ] Teste: 6 segmentos fake de 10 s, pico no 50º s → saída 55 s ±0.5; com pre+post=90 → limitado a 60 s; (Review 2) pico no 5º s → começa em 0, sem erro.
- [ ] Implementar; commit. (O worker espera `post_s` + 1 segmento antes de cortar.)

### Task 6: Transcrição e tradução
**Files:** Create `clipador/transcribe.py`, `clipador/llm.py`; Test `tests/test_llm.py`, `tests/test_transcribe.py`
**Interfaces — Produces:**
- `transcribe(path, model) -> Transcript(lang:str, segments:list[Seg(start,end,text,words:list[Word(start,end,text)])])` (faster-whisper, `word_timestamps=True`, `vad_filter=True`).
- `llm.complete(prompt, system) -> str|None` (Anthropic `claude-haiku-4-5` se chave; senão Ollama; senão None).
- `translate_segments(segs, src_lang) -> list[Seg]` (um pedido em JSON com todos os segmentos; mantém start/end; se None → mantém original).
- [ ] Teste com `llm.complete` mockado: tradução preserva número e tempos dos segmentos; resposta JSON inválida → retorna original.
- [ ] Teste de fumaça do transcribe (marcado `slow`) com áudio TTS curto.
- [ ] Implementar; commit.

### Task 7: Textos do post
**Files:** Create `clipador/texts.py`; Test `tests/test_texts.py`
**Interfaces — Produces:** `make_texts(streamer, transcript_pt:str, live_title:str, base_tags:list[str]) -> PostTexts(card:list[str], title:str, description:str, hashtags:list[str])`
- Prompt pede JSON: card (1–2 linhas, ≤42 chars cada, PT-BR, narrando o que aconteceu), título (≤70 chars, gancho), descrição (2 frases + crédito "🎥 Live de {name} em {plataforma}"), 5–8 hashtags.
- Pós-processo obrigatório: quebra/corta card para ≤2 linhas ≤42 chars; força `#GTA6` e `#{name}` nas hashtags; mescla base_tags (dedupe, máx 12).
- [ ] Testes: LLM devolve card de 3 linhas → saída 2 linhas ≤42; LLM None (Review 4) → fallback por template; transcrição vazia (Review 3) → usa live_title.
- [ ] Implementar; commit.

### Task 8: Legendas ASS
**Files:** Create `clipador/subtitles.py`; Test `tests/test_subtitles.py`
**Interfaces — Produces:** `build_ass(transcript, mode:Literal["karaoke","phrase"], area:Rect, accent:str) -> str`
- karaoke: blocos de até 3 palavras; palavra ativa na cor de destaque (`\k` / override de cor por evento). phrase: um evento por segmento, quebra em ≤2 linhas de ≤32 chars. Fonte Montserrat ExtraBold (fallback Arial Black), contorno 6, centro da área da gameplay a 70% da altura.
- [ ] Testes: 7 palavras → 3 eventos com tempos corretos; phrase longa → ≤2 linhas; transcrição vazia → ASS sem eventos (válido).
- [ ] Implementar; commit.

### Task 9: Render vertical e capa
**Files:** Create `clipador/render.py`, `assets/fonts/` (Montserrat OFL); Test `tests/test_render.py`
**Interfaces — Produces:** `render_vertical(src, out, webcam_rect|None, card_lines, ass_path, handle, accent) -> Path`; `make_cover(src, peak_offset_s, card_lines, out_png) -> Path`.
- filter_complex: crop webcam → scale 1080x608; gameplay = crop central 16:9 sem a área da cam → scale 1080x1052 (força `force_original_aspect_ratio=increase`+crop); card = `color` 1080x260 escuro `#0E0E12` + barra de destaque 8 px + `drawtext` das 2 linhas + @handle pequeno; vstack; `ass=` sobre a saída. Sem webcam (Review 5): card no topo + gameplay sobre fundo blur.
- [ ] Teste: vídeo testsrc 1280x720 20 s → saída 1080x1920, duração igual ±0.2 s, tem áudio; idem sem webcam; capa 1080x1920 PNG existe.
- [ ] Implementar; commit.

### Task 10: Orquestrador
**Files:** Create `clipador/pipeline.py`, `clipador/runner.py`, `clipador/__main__.py`; Test `tests/test_pipeline.py`
**Interfaces — Produces:** `process_clip(streamer, segments, peak_ts, settings) -> Clip` (5→6→7→8→9→db). `Runner(settings, streamers)`: vigia a cada 60 s, sobe/derruba `StreamerWorker` (Recorder+ChatReader+HypeDetector), fila única para `process_clip`, respeita `max_live` e `GlobalLimiter`. CLI: `python -m clipador run|dashboard|test-clip <video>`.
- [ ] Teste: `process_clip` ponta a ponta com segmentos fake e LLM/whisper mockados → linha no DB com vídeo e capa existentes.
- [ ] `test-clip` permite o Davi testar com qualquer vídeo local sem live.
- [ ] Implementar; commit.

### Task 11: Painel e configurador de webcam
**Files:** Create `clipador/web/app.py`, `clipador/web/static/{index.html,app.js,style.css,webcam.html}`; Test `tests/test_web.py`
**Interfaces — Consumes:** `db.*`, `load_config`. **Produces:** `GET /api/clips?posted=`, `POST /api/clips/{id}/posted`, `GET /media/...`, `GET /api/streamers`, `POST /api/streamers/{name}/snapshot` (frame via streamlink+ffmpeg), `PUT /api/streamers/{name}/webcam`.
- Painel: grade de cards com `<video>` 9:16, card/título/descrição/hashtags, botões "Baixar vídeo", "Baixar capa", "Copiar texto do post", "Postado"; atualiza a cada 5 s; filtro por streamer. Configurador: frame da live, arrastar retângulo, salvar.
- [ ] Testes com TestClient: lista, marcar postado, salvar webcam persiste no YAML.
- [ ] Implementar; commit.

### Task 12: Instalação Windows e verificação final
**Files:** Create `install.bat`, `iniciar.bat`, `README.md`
- `install.bat`: checa Python 3.11 (`winget install Python.Python.3.11`), ffmpeg (`winget install Gyan.FFmpeg`), cria venv, `pip install -e .`, copia `.env.example`.
- `iniciar.bat`: sobe `dashboard` e `run`, abre `http://localhost:8765`.
- README em PT-BR: instalar, colocar a chave, configurar webcam, testar com `test-clip`, avisos de monetização/direitos.
- [ ] Rodar suíte completa; gerar um clipe de exemplo com vídeo sintético e conferir o vídeo visualmente (frames extraídos); commit.
