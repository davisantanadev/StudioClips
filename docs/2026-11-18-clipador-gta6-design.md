# Clipador GTA 6 — Design (aprovado em 2026-11-18)

## Intenção
Davi quer ganhar dinheiro com cortes de lives de streamers famosos jogando GTA 6 no lançamento, postando no TikTok, Instagram Reels e YouTube Shorts **antes** dos outros clipadores. Por isso o sistema corta em tempo real, enquanto a live acontece, e entrega o clipe pronto: editado, legendado, com card, capa, título, descrição e hashtags. Davi só baixa e posta.

## Metas
- Clipe pronto ~1–2 min depois do momento acontecer.
- Formato vertical 9:16: webcam do streamer em cima, card no meio (máx. 2 linhas, PT-BR, "aconteceu isso, isso e isso"), gameplay embaixo, legenda sobre a gameplay.
- Duração máxima: **60 s** (padrão: 40 s antes do pico + 15 s depois).
- Capa, título, descrição e hashtags gerados para cada clipe.
- Painel local no navegador para baixar vídeo/capa e copiar textos; marcar "postado".

## Não-metas (desta versão)
- Postar automaticamente nas redes (exige aprovação de API em cada uma).
- Detecção automática de rosto/webcam.
- Ler hashtags em alta em tempo real.

## Anti-metas
- Clipe chegar atrasado (perde o propósito).
- Spam de clipes do mesmo momento (cooldown de 2 min por streamer; teto de ~6 clipes/hora no total).
- Travar o PC do Davi.

## Restrições
- Roda no **Windows** do Davi. Python 3.10–3.12, ffmpeg, streamlink.
- Lives em 720p, gravadas sem recodificar.
- Chave da Anthropic num `.env` local (nunca no chat). Fallback: Ollama local.
- Banco local SQLite (sem Supabase nesta versão).

## Streamers
- **BR (legenda palavra por palavra, karaokê):** Alanzoka, Davy Jones, Bistecone, Cellbit, Blackoutz.
- **Estrangeiros (legenda traduzida PT-BR frase por frase; card em PT-BR):** DjMaRiiO (espanhol), eveleone2004 (link pendente), xQc (EN), Buster (RU), Trymacs (DE), TypicalGamer (EN), MontanaBlack88 (DE).
- Plataformas (Twitch / Kick / YouTube) conferidas na construção; configuráveis em `streamers.yaml`.

## Abordagens consideradas
- **A. Local no Windows + painel no navegador — ESCOLHIDA.** Custo ~zero, menor latência, sem terceiros. Exige PC ligado.
- B. Nuvem via VideoDB: alivia o PC, mas custa créditos por hora e tem latência maior.
- C. Gravar tudo e clipar depois: é o atraso que o Davi quer evitar.

## Desenho
1. **Vigia:** a cada 60 s checa quem está ao vivo (preferindo GTA 6).
2. **Gravação:** streamlink → ffmpeg em segmentos de 10 s (`-c copy`), buffer rolante de 3 min por streamer.
3. **Detector de hype:** taxa de mensagens do chat (Twitch IRC anônimo, Kick websocket, YouTube live chat) vs. média móvel do canal + pico de volume do áudio. Cooldown 2 min/streamer; limite global por hora.
4. **Corte:** junta segmentos do buffer, recorta [pico−40 s, pico+15 s], teto 60 s.
5. **Transcrição:** Whisper (faster-whisper) com tempo por palavra; idioma detectado.
6. **Tradução (estrangeiros):** Claude traduz segmentos para PT-BR; legenda por frase com o tempo do segmento.
7. **Textos:** Claude gera card (≤2 linhas), título, descrição, hashtags (lista base editável + #GTA6 + streamer).
8. **Render:** ffmpeg filter_complex — crop da webcam (coordenadas por streamer) em cima, faixa do card no meio, gameplay embaixo, legenda ASS. Sem webcam: só gameplay + card. Capa = frame do pico + texto do card.
9. **Painel:** FastAPI + HTML em localhost; lista, prévia, baixar, copiar textos, marcar postado.
10. **Configurador de webcam:** captura um frame da live; Davi arrasta um retângulo; salva no `streamers.yaml`.

## Decidido pelo Claude (corrigível)
- Card escuro com uma cor de destaque + @ do Davi (pendente).
- Whisper `small` por padrão; `base` e limite de 4 lives se o PC for fraco.
- Até 6 clipes/hora no total.

## Pendências do Davi
- Link do canal do eveleone2004.
- @ para o card.
