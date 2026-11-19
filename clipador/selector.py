"""A IA (Groq) escolhe o melhor trecho (~1 min, até 90 s quando a história pede) dentro da janela de análise e já escreve card e título."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from . import ai
from .ai import Seg, Transcript, Word
from .detector import CLIP_SPEECH_RE

log = logging.getLogger(__name__)

SYSTEM = (
    "Você é o editor-chefe do maior canal brasileiro de cortes de GTA 6 no TikTok, Reels e Shorts, com "
    "dezenas de milhões de views. Você só publica o que PARA O SCROLL e faz a pessoa mandar pro amigo. "
    "Recusa sem dó momento aleatório de live. Nunca inventa fato que não está na fala ou no chat. "
    "Escreve em português do Brasil informal, como os melhores perfis de cortes: direto, forte, com gíria, "
    "sem narrar em terceira pessoa. "
    "Responde SOMENTE em JSON."
)

VIRAL_RULES = (
    "O QUE VIRALIZA (só isso merece nota alta):\n"
    "- MUITO ENGRAÇADO: piada, falha ridícula, bug absurdo, NPC fazendo besteira, streamer chorando de rir; "
    "RISADA CONTAGIANTE (o streamer ou alguém da call rachando de rir, crise de riso) já vale nota alta; "
    "o chat enche de KKKK/KEKW.\n"
    "- SUSTO / JUMPSCARE: o streamer pula, grita, se assusta de verdade.\n"
    "- AÇÃO INTENSA: perseguição, tiroteio, explosão, fuga de 5 estrelas, acidente absurdo, morte inesperada, "
    "manobra impossível que deu certo (ou deu muito errado).\n"
    "- RAIVA / REAÇÃO EXTREMA: streamer gritando, xingando o jogo, rage, desespero.\n"
    "O QUE NÃO SERVE (nota 0-4, mesmo com chat rápido): conversa com o chat, leitura de doação/sub/presente, "
    "agradecimento, propaganda, explicação de mecânica, menu/mapa/loading, cutscene sem reação do streamer, "
    "streamer só andando, conversa calma, fala solta sem acontecimento.\n"
)

TONE_RULES = (
    "TOM (vale pro card, título e descrição): português do Brasil INFORMAL, do jeito que o admin de uma página "
    "grande de cortes escreve, ou usando a fala mais forte do próprio streamer. TUDO em português: fala em "
    "inglês/espanhol citada no card ou no título vai TRADUZIDA (\"oh my god no way\" vira \"MEU DEUS, NÃO\"). "
    "Pode falar com quem assiste (\"espera o final\", \"olha isso\") e usar gíria (mano, véi, KKKK, deu ruim, "
    "do nada). NUNCA terceira pessoa narrando (\"{d} faz X\", \"ele tenta…\", \"o jogador…\") e NUNCA tom de "
    "jornal ou de robô (\"a situação sai do controle\", \"mostra como\", \"deixa todo mundo em choque\", "
    "\"de forma inesperada\", \"jogabilidade\")."
)

CARD_RULES = (
    TONE_RULES + "\n"
    "CARD (texto grande no topo do vídeo, os primeiros 3 segundos decidem se a pessoa fica): 1 ou 2 linhas, "
    "até ~26 caracteres cada (máx. 42), sem emoji. O GANCHO do momento, específico, que dá vontade de ver o "
    "final. PROIBIDO: \"chat\", \"sub\", \"inscrito\", \"doação\", \"foi à loucura\", \"explodiu\", \"reagiu\", "
    "\"surtou\", \"momento épico/insano\", \"no GTA 6\" sozinho, \"o streamer\". "
    "Bons exemplos: [\"TOCOU O ALARME\", \"E O DONO TAVA EM CASA\"], [\"\\\"EU NÃO ACREDITO\", \"NISSO, MANO\\\"\"], "
    "[\"NINGUÉM ESPERAVA\", \"ESSE JACARÉ\"], [\"FUGIU DA POLÍCIA\", \"DE BICICLETA KKKK\"], "
    "[\"5 ESTRELAS\", \"EM 10 SEGUNDOS\"], [\"ESPERA O FINAL\", \"DESSA FUGA\"]."
)

TITLE_RULES = (
    "TÍTULO DO POST (até ~70 caracteres, aparece cortado depois de ~60): VARIE o formato de um clipe pro outro, "
    "nada de molde fixo. Use o que combinar com o momento:\n"
    "  pergunta: \"Quem deixou ele dirigir?? 😂\"\n"
    "  fala do streamer entre aspas: \"\\\"EU NÃO ACREDITO NISSO\\\" 💀\"\n"
    "  POV: \"POV: você confia no {d} no GTA 6 🚗\"\n"
    "  reação: \"Mano, a polícia chegou em 3 segundos 😭\"\n"
    "  curiosidade: \"Ninguém sabia que dava pra fazer isso no GTA 6 🤯\"\n"
    "Pode citar {d} ou \"GTA 6\" quando ficar natural, não é obrigatório. Até 1 emoji que combine.\n"
    "DESCRIÇÃO: 2 ou 3 frases curtas e informais, cada uma com função diferente: (1) o gancho, falando com quem "
    "assiste; (2) uma curiosidade ou o detalhe mais engraçado do momento — SÓ fato que está na fala, no chat ou "
    "nas imagens (o que alguém falou, o que deu errado, quem rachou de rir). NUNCA invente mecânica, recurso, "
    "regra do jogo ou motivo; se não tiver nada assim, pule essa frase. Não use sempre o rótulo \"Curiosidade:\" "
    "(varie: \"detalhe:\", \"repara que…\", \"e o pior…\", ou sem rótulo); (3) uma pergunta pra galera comentar, "
    "diferente a cada post. Ex.: \"Mano, o cara tava DENTRO de casa 💀 E o pior: ele ainda tentou se esconder "
    "no armário. Você teria fugido ou encarado?\".\n"
    "HASHTAGS: 3 a 4 bem específicas do que acontece no corte, em PT-BR quando fizer sentido "
    "(ex.: #perseguicao, #jumpscare, #fail, #assalto, #humor, #vicecity). Sem #GTA6 (já entra sozinha)."
)
@dataclass
class Selection:
    start: float
    end: float
    score: float | None = None
    reason: str = ""
    card: list[str] = field(default_factory=list)
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    by_ai: bool = False

    @property
    def dur(self) -> float:
        return self.end - self.start


def slice_transcript(t: Transcript, start: float, end: float) -> Transcript:
    """Pega só o trecho [start, end] e zera os tempos no início do clipe."""
    segs = []
    for s in t.segments:
        if s.end <= start or s.start >= end:
            continue
        words = [Word(max(w.start, start) - start, min(w.end, end) - start, w.text)
                 for w in s.words if w.end > start and w.start < end]
        text = " ".join(w.text for w in words) if s.words else s.text
        if text.strip():
            segs.append(Seg(max(s.start, start) - start, min(s.end, end) - start, text.strip(), words))
    return Transcript(t.lang, segs)


def fala_text(t: Transcript) -> str:
    """Fala com tempo; marca [CLIPA] onde o streamer pede clipe ("clipa isso", "clip that", "corta isso")."""
    return "\n".join(f"[{s.start:.1f}-{s.end:.1f}] " + ("[CLIPA] " if CLIP_SPEECH_RE.search(s.text) else "") + s.text
                     for s in t.segments) or "(sem fala, só gritos/música)"


def _chat_timeline(chat: list[tuple[float, str]], bucket: float = 5.0, samples: int = 4) -> str:
    groups: dict[int, list[str]] = defaultdict(list)
    for t, text in chat:
        groups[int(t // bucket)].append(text.strip()[:60])
    lines = []
    for k in sorted(groups):
        msgs = groups[k]
        uniq = list(dict.fromkeys(m for m in msgs if m))[:samples]
        lines.append(f"[{k * bucket:.0f}s] {len(msgs)} msgs: " + " | ".join(uniq))
    return "\n".join(lines[-40:])


def snap(sel_start: float, sel_end: float, t: Transcript, total: float, peak: float,
         min_s: float, max_s: float) -> tuple[float, float]:
    """Ajusta o trecho: dura entre min_s e max_s, cabe no vídeo, não corta frase no meio."""
    min_s = min(min_s, total)
    start, end = max(0.0, sel_start), min(total, sel_end)
    if end <= start:
        start, end = max(0.0, peak - 35), min(total, peak + 15)
    # começa no início de uma frase (até 3 s antes) e termina no fim de uma (até 3 s depois)
    for s in t.segments:
        if s.start <= start < s.end and start - s.start <= 3:
            start = s.start
            break
    for s in t.segments:
        if s.start < end <= s.end and s.end - end <= 3:
            end = s.end
            break
    end = min(end + 0.6, total)  # respiro depois da última palavra
    if end - start > max_s:
        end = start + max_s
    if end - start < min_s:  # estica pra frente, depois pra trás
        end = min(total, start + min_s)
        start = max(0.0, end - min_s)
    return round(start, 2), round(end, 2)


def _pick(j: dict) -> dict | None:
    """Aceita {"candidatos": [...]} (fica o de maior nota) ou um corte só no próprio JSON."""
    cands = j.get("candidatos")
    if isinstance(cands, list):
        cands = [c for c in cands if isinstance(c, dict) and "inicio" in c and "fim" in c]
        if not cands:
            return None
        return max(cands, key=lambda c: float(c.get("nota", 0) or 0))
    return j


def select_moment(t: Transcript, chat: list[tuple[float, str]], peak: float, total: float, *,
                  display: str, live_title: str, min_s: float, max_s: float, model: str,
                  signals: str = "", ideal_s: float | None = None,
                  recent_titles: list[str] | None = None) -> Selection:
    """`peak`, tempos do chat e da transcrição: segundos dentro da janela de análise.
    `signals`: o que o detector viu (risada no chat, susto, grito no áudio)."""
    fala = fala_text(t)
    fala = fala[-7000:]  # economiza cota grátis da IA
    ideal = min(ideal_s or max_s, max_s)
    ideal_min = max(min_s, ideal - 8)
    user = (
        f"Streamer: {display} jogando GTA 6. Título da live: {live_title or '-'}\n"
        f"Janela de {total:.0f}s da live. O pico foi perto de {peak:.0f}s"
        + (f" — sinais detectados: {signals}" if signals else "") + ".\n"
        f"Idioma da fala: {t.lang or '?'}\n\n"
        f"FALA (segundos):\n{fala}\n\nCHAT (a cada 5s: quantidade e exemplos):\n{_chat_timeline(chat) or '-'}\n\n"
        + VIRAL_RULES +
        "\nTAREFA: proponha até 3 cortes diferentes desta janela e dê nota sincera a cada um.\n"
        f"- Duração: o ideal é perto de {ideal:.0f} s ({ideal_min:.0f} a {ideal:.0f} s): contexto curto antes e a "
        f"reação inteira depois. Só faça mais curto (mínimo {min_s:.0f} s) se o resto for enrolação"
        + (f"; só passe de {ideal:.0f} s (até {max_s:.0f} s, nunca mais) se a história PRECISAR: contexto "
           "longo pra entender ou reação que continua forte" if max_s > ideal else "") + ".\n"
        "- Comece no início de uma frase (nunca no meio de uma fala) e termine logo depois da reação.\n"
        "- O corte tem que incluir o pico (o acontecimento).\n"
        "- [CLIPA] na fala = o próprio streamer pediu clipe ali: sinal forte de que algo bom acabou de acontecer; "
        "inclua o que aconteceu logo ANTES do pedido (o pedido em si pode ficar no fim ou sair).\n"
        "NOTA (seja MUITO duro; a maioria dos momentos de live é 4-6 e não deve ser postada):\n"
        "  9-10: viraliza com certeza: muito engraçado, susto real ou ação absurda, qualquer um entende;\n"
        "  7-8: acontecimento forte e claro com reação forte do streamer;\n"
        "  5-6: tem algo, mas fraco, lento ou depende de contexto;\n"
        "  0-4: momento aleatório / lista do que NÃO SERVE acima.\n"
        + CARD_RULES.replace("{d}", display) + "\n"
        + TITLE_RULES.replace("{d}", display) + "\n"
        + ("TÍTULOS QUE JÁ SAÍRAM (não repita o formato nem as palavras de abertura): "
           + " | ".join(recent_titles[:8]) + "\n" if recent_titles else "") + "\n"
        "Responda JSON:\n"
        '{"candidatos": [{"inicio": seg, "fim": seg, "nota": 0-10, '
        '"tipo": "engraçado|susto|ação|raiva|fraco", '
        '"motivo": "1 frase: o que acontece e por que viraliza (ou não)", '
        '"card": ["linha 1", "linha 2"], "titulo": "...", "descricao": "...", "hashtags": ["#.."]}]}'
    )
    j = ai.chat_json(SYSTEM, user, model, temperature=0.4)
    best = _pick(j) if isinstance(j, dict) else None
    if isinstance(best, dict):
        try:
            st, en = snap(float(best["inicio"]), float(best["fim"]), t, total, peak, min_s, max_s)
            card = best.get("card") or []
            tags = best.get("hashtags") or []
            return Selection(st, en, float(best.get("nota", 0) or 0), str(best.get("motivo", ""))[:200],
                             card if isinstance(card, list) else [str(card)], str(best.get("titulo", "")).strip(),
                             str(best.get("descricao", "")).strip(),
                             tags if isinstance(tags, list) else str(tags).split(), by_ai=True)
        except (KeyError, TypeError, ValueError):
            log.warning("IA devolveu um corte inválido, usando o padrão")
    st, en = snap(peak - 43, peak + 15, t, total, peak, min_s, max_s)
    return Selection(st, en)
