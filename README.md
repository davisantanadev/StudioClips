# StudioClips — Clipador GTA 6

Fica de olho nas lives dos streamers da lista e, quando o chat explode, a IA escolhe o melhor trecho (perto de 1 minuto, com começo, acontecimento e reação) e entrega o vídeo vertical pronto em cerca de 1 minuto: o jogo recortado em pé seguindo a ação, webcam do streamer em cima (achada sozinha), card contando o que aconteceu (some depois de 4 s), legenda e seu @ como marca d'água. Você só baixa e posta no TikTok, Reels e Shorts.

## Instalar o app (Windows 10/11, sem instalar Python)

1. Baixe o **`StudioClips-windows.zip`** em [**Releases**](https://github.com/davisantanadev/StudioClips/releases/latest).
2. Descompacte onde quiser (ex.: `C:\StudioClips`). Não rode de dentro do zip.
3. Dê dois cliques em **`StudioClips.exe`**. O painel abre numa **janela própria** (não usa o navegador).
   - Se aparecer "O Windows protegeu o computador": **Mais informações → Executar assim mesmo** (o app não é assinado).
   - O FFmpeg já vem junto. A janela usa o WebView2, que já vem no Windows 11 (no Windows 10, se faltar, o painel abre no navegador).
4. No painel, vá em **Ferramentas → Configurações**:
   - cole a **sua** chave **grátis** do Groq (<https://console.groq.com>, login com o Google, sem cartão: **API Keys → Create API Key**) e clique em **Testar**;
   - coloque o seu @.

A chave e os ajustes ficam em `%APPDATA%\ClipadorGTA6`, fora da pasta do app: **atualizar não apaga a chave, o @ nem as webcams marcadas.** Nenhuma chave vem no app nem neste repositório; cada pessoa usa a sua.

O Groq faz a legenda (Whisper), escolhe o corte, escreve o card (olhando quadros do vídeo), o título, as hashtags e a tradução dos gringos, tudo na nuvem, sem pesar o seu PC.

> O app não traz o Whisper local (plano B quando o Groq cai), pra ficar ~300 MB menor. Quem precisa dele pode rodar pelo código-fonte (abaixo).

## Rodar pelo código-fonte (opcional)

1. Baixe o código (**Code → Download ZIP**) e descompacte.
2. Dê dois cliques em **`install.bat`** (instala Python 3.12 e FFmpeg pelo winget, se faltar; se ele instalar algum, feche e rode de novo).
3. Dê dois cliques em **`INICIAR.bat`**. Se preferir, cole `GROQ_API_KEY=...` no `.env` (copie do `.env.example`): na primeira vez que abrir, ela é copiada pra `%APPDATA%`.

Para gerar o `.exe` você mesmo: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` (cria `dist\StudioClips-windows.zip`). Cada tag `v*` enviada ao GitHub monta e publica o zip em Releases automaticamente (`.github/workflows/release.yml`).

## Usar

- Abra o **`StudioClips.exe`** (ou o `INICIAR.bat`). **Fechar a janela desliga tudo** (gravações e edições). Abrir de novo com ele ligado só abre outra janela do painel.
- A cada minuto o sistema checa quem da lista está ao vivo jogando GTA e começa a gravar sozinho.
- Quando rola um momento, o clipe aparece no painel em **Para postar**. Lá tem os botões:
  - **⬇ Vídeo** e **🖼 Capa**
  - **📋 Texto do post**: copia título, descrição e hashtags
  - **✅ Postado**: tira o clipe da fila
  - **Editar**: muda título e hashtags, ou reescreve o card (o vídeo é refeito)
- **✂️ Cortar agora**: se você está assistindo e viu algo bom, aperte. Ele corta os últimos ~50 segundos.

### Webcam: automática

Não precisa marcar nada. Em cada clipe, o Clipador procura o rosto do streamer nos quadros (com um detector de rosto que roda no seu PC, grátis): o rosto da webcam fica parado no mesmo canto enquanto os personagens do jogo se mexem. Depois encosta a caixa na moldura da câmera (sem mostrar a borda) ou, se a câmera for recortada com fundo verde, pega cabeça e ombros. Como é feito clipe a clipe, ele acompanha quando o streamer troca de cena ou muda a câmera de lugar.

- Câmera em tela cheia (conversa sem jogo) ou sem rosto visível: o clipe sai só com o jogo.
- Câmera muito pequena na live (menos de 200 px): funciona, mas sai um pouco borrada no vertical.
- Se em algum streamer não ficar bom, dá pra marcar à mão em **🎥 Webcams** (a marcação manual vale por cima da automática), ou desligar em **Ferramentas → Configurações**.

### Clipar uma live que já acabou (VOD)

No painel, em **Clipar live gravada**, cole o link da VOD (Twitch, YouTube ou Kick): o Clipador **descobre sozinho de quem é** e já escolhe na lista. Se for alguém que não está na lista, ele entra sozinho (fica salvo em `%APPDATA%\ClipadorGTA6\streamers_extra.yaml`). Depois é só escolher quantos clipes quer. Ele lê o replay do chat, acha os picos e baixa só os trechos. Para VOD da Kick, ele usa o volume do áudio.

Pelo terminal também funciona:
```
start /wait StudioClips.exe vod https://www.twitch.tv/videos/123456 -s alanzoka -n 8   (app, no cmd)
.venv\Scripts\python -m clipador vod https://www.twitch.tv/videos/123456 -s alanzoka -n 8   (código-fonte)
```

### Testar sem live

```
.venv\Scripts\python -m clipador teste meu_video.mp4 -s alanzoka --pico 30
.venv\Scripts\python -m clipador status
```
O `teste` gera um clipe a partir de um vídeo seu, usando como pico o segundo informado. O `status` mostra quem está ao vivo agora.

## Quem ele vigia

- **A lista** (`streamers.yaml`): mais de 40 streamers e youtubers conferidos (Alanzoka, Davy Jones no GameplayRJ, Paulinho o LOKO, Piuzinho, Gaules, Kai Cenat, IShowSpeed…), cada um com os canais dele na Twitch, na Kick e no YouTube.
- **As lives de GTA mais vistas agora**: de 5 em 5 minutos o Clipador olha a categoria de GTA VI na Twitch e na Kick e as lives de "GTA 6" no YouTube, e vigia também as mais vistas que não estão na lista (até 8, com pelo menos 3.000 pessoas assistindo, em português, inglês ou espanhol). No painel elas aparecem como **🔎 achado sozinho**. Liga/desliga e muda o mínimo em **Ferramentas → Configurações**.

## O que dispara um corte

- **Chat explodindo com reação:** risada (KKKK, KEKW), susto (WTF, ???) ou o streamer gritando.
- **Chat pedindo clipe:** várias pessoas mandando "CLIPA", "clip it", "!clip" ou "corta isso" ao mesmo tempo.
- **Clipes da Twitch (opcional):** quando 2 ou mais espectadores criam clipes do mesmo trecho em ~2 minutos, o Clipador corta esse momento. Na VOD da Twitch, os trechos que viraram clipe ganham prioridade. Para ligar, crie um app grátis em <https://dev.twitch.tv/console/apps> (nome qualquer, URL `http://localhost`, categoria "Other"), gere um Client Secret e cole o Client ID e o Secret em **Ferramentas → Configurações** (botão **Testar Twitch**). Eles ficam em `%APPDATA%\ClipadorGTA6\.env`, nunca no repositório.
- **Kick:** o chat da Kick funciona de novo. A Kick troca a chave do chat de tempos em tempos, e o Clipador pergunta a chave atual a cada conexão.

## IA grátis e sem limite de clipes

Não há limite de clipes por hora: cada streamer só tem um intervalo mínimo entre clipes (pra não repetir o mesmo momento), e 2 clipes são editados ao mesmo tempo.

A IA usa o plano grátis de vários serviços em rodízio. Quando um modelo bate o limite do minuto, o próximo assume na hora:
- **Groq** (legenda, texto e visão): <https://console.groq.com/keys>
- **Cerebras** (texto, opcional): <https://cloud.cerebras.ai>
- **Google Gemini** (texto e visão, opcional): <https://aistudio.google.com/apikey>

Cole as chaves em **Ferramentas → Configurações** e clique em **Testar chaves**. Se toda a IA estiver no limite ao mesmo tempo, a legenda sai pelo Whisper local e o corte segue o pico detectado.

## Como a IA escolhe o corte

Quando o chat explode, o sistema pega cerca de 2 minutos ao redor do momento (90 s antes e 25 s depois) e transcreve tudo com o Whisper large-v3. A IA do Groq lê a fala e o chat como um editor exigente: propõe até 3 cortes, cada um com **contexto → acontecimento → reação**, dá uma **nota de 0 a 10** a cada um e fica com o melhor. O corte mira **perto de 1 minuto** (52 a 60 s), só fica mais curto (mínimo 45 s) se o resto for enrolação e só passa de 1 minuto (**até 90 s**) quando a história precisa de mais contexto ou a reação continua forte. Se o streamer falar "clipa isso", "clip that" ou "corta isso", a IA trata como sinal forte. Começa e termina em frase inteira.

Clipes com nota abaixo de `min_score` (padrão **7**) são descartados; o painel mostra o motivo em "Ao vivo". O botão "Cortar agora" e o teste nunca descartam.

**Card:** depois do corte, a IA "assiste" o clipe (3 quadros: começo, meio e fim) junto com a fala e escreve a manchete contando o que aconteceu de verdade: quem fez o quê na 1ª linha e a virada na 2ª (em destaque). Card genérico ("X no GTA 6", "momento insano") é recusado.

**Modelos:** texto `openai/gpt-oss-120b`, visão `qwen/qwen3.8-27b`, legenda `whisper-large-v3`. Se o Groq aposentar um modelo, o Clipador troca sozinho por outro disponível (não precisa mexer em nada).

## Enquadramento vertical

O jogo sempre sai **em pé**, nunca como faixa deitada. O Clipador olha o clipe em miniatura, descobre onde está o jogo (deixa de fora bordas pretas, painel de chat, overlay parado e a webcam) e move uma "câmera" suave que segue a ação, sem trancos.

- **Webcam em cima, jogo logo abaixo.** Sem webcam, só o jogo.
- **Zoom do jogo** (Ferramentas → Configurações):
  - **Aberto** (padrão): mostra bem mais do jogo (cerca de 70% da largura da tela da live); o espaço que sobra em cima e embaixo é o próprio jogo desfocado.
  - **Médio**: recorte quadrado.
  - **Fechado**: o jogo preenche toda a tela, com mais zoom.
- O recorte do jogo foge do lado da webcam, pra ela não aparecer duas vezes.

## Ajustes (`streamers.yaml`)

| Campo | O que faz |
|---|---|
| `pre_s` / `post_s` | segundos antes e depois do pico quando a IA não está disponível (padrão 43 + 15) |
| `min_clip_s` / `ideal_clip_s` / `max_clip_s` | duração mínima, ideal e máxima do clipe (padrão 45, 60 e 90 s; teto 90) |
| `min_score` | nota mínima da IA pra virar clipe (padrão 7; menor = mais clipes, maior = só os melhores). Também muda no painel |
| `hype_threshold` | sensibilidade: menor gera mais clipes (2.5), maior gera só os momentos enormes (4) |
| `cooldown_s` | tempo mínimo entre dois clipes do mesmo streamer |
| `require_gta` | `true` grava só quando o título ou a categoria fala de GTA |
| `card_seconds` | tempo até o card sumir com transição (padrão 4 s; `0` = nunca some) |
| `accent` | cor de destaque |
| `hashtags.txt` | hashtags base, que entram em todo post (sem o arquivo, usa a lista padrão) |

O `@`, a chave e a nota mínima ficam no painel e valem por cima do `streamers.yaml`. Se o `streamers.yaml` sumir ou ficar vazio, o Clipador usa a lista padrão e recria o arquivo.

Para adicionar um streamer, copie um bloco existente. Cada streamer aceita vários canais (Twitch, Kick, YouTube) e o sistema grava o que estiver ao vivo.

## Como funciona

```
vigia (1/min) ─► streamlink ─► ffmpeg (segmentos de 10 s, buffer de 3 min, sem recodificar)
chat (Twitch IRC / Kick / YouTube) + volume ─► detector de hype (compara com a média do canal)
pico ─► janela de ~2 min ─► Groq Whisper (fala) ─► IA escolhe o melhor corte (~1 min) e dá nota
     ─► tradução PT-BR (gringos) ─► IA com visão escreve o card ─► enquadramento automático
     ─► ffmpeg 9:16 (NVENC da GTX) ─► painel
```
- Sem chave do Groq ou com o Groq fora do ar: a legenda sai pelo Whisper local, o corte é fixo em volta do pico e o card recebe um texto padrão, que você edita no painel. O painel avisa no topo quando falta a chave.
- Seu PC só grava, sem recodificar, e monta o vídeo final na placa de vídeo. 14 lives em 720p usam cerca de 45 Mbps de internet. Se ficar pesado, baixe `max_live` ou troque `quality` para `480p,best`.

## Avisos

- **Direitos e monetização:** os clipes usam a imagem e a voz dos streamers e gameplay da Rockstar/Take-Two. Prefira streamers que liberam cortes. Conteúdo "reaproveitado" pode ter a monetização negada, e vídeos podem levar strike. O card, a legenda traduzida e a edição ajudam a caracterizar transformação, mas não garantem nada.
- O detector erra às vezes. Assista o clipe antes de postar.
- Os arquivos ficam em `data\clips`, na pasta do app. Apague os antigos de vez em quando.
- Painel leve: com a janela minimizada ele para de atualizar e de animar; ao voltar, atualiza na hora.
