"""Card (≤2 linhas), título, descrição, hashtags e tradução das legendas — tudo em PT-BR."""
from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass

from . import ai
from .ai import Seg, Transcript
from .lang import is_foreign
from .selector import CARD_RULES, TITLE_RULES

CARD_MAX_CHARS = 42
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+")


def strip_emoji(s: str) -> str:
    return EMOJI_RE.sub("", s)
LANG_PT = {"en": "inglês", "es": "espanhol", "de": "alemão", "ru": "russo", "fr": "francês", "pt": "português"}

SYSTEM = (
    "Você é o admin do maior perfil brasileiro de cortes de GTA 6 (TikTok, Reels, Shorts). "
    "Escreve como gente: informal, direto, com gíria brasileira, específico e sem inventar fatos. "
    "Nunca narra em terceira pessoa nem escreve como jornal. "
    "Responde SOMENTE em JSON."
)


@dataclass
class PostTexts:
    card: list[str]
    title: str
    description: str
    hashtags: list[str]


def fit_card(lines: list[str] | str, max_chars: int = CARD_MAX_CHARS) -> list[str]:
    """Garante no máximo 2 linhas de até `max_chars` caracteres."""
    if isinstance(lines, list):
        clean = [re.sub(r"\s+", " ", strip_emoji(str(l))).strip() for l in lines]
        clean = [l for l in clean if l]
        if 0 < len(clean) <= 2 and all(len(l) <= max_chars for l in clean):
            return clean  # respeita a quebra de linha escolhida
    text = " ".join(map(str, lines)) if isinstance(lines, list) else str(lines)
    text = re.sub(r"\s+", " ", strip_emoji(text)).strip()
    if not text:
        return []
    wrapped = textwrap.wrap(text, max_chars, break_long_words=True)
    if len(wrapped) <= 2:
        return wrapped
    first = wrapped[0]
    rest = " ".join(wrapped[1:])
    second = rest if len(rest) <= max_chars else rest[: max_chars - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return [first, second]


GENERIC_RE = re.compile(
    r"^(no|jogando|joga|live|ao vivo)?\s*(o\s+)?(gta|gta ?6|gta ?vi)\b|momento (insano|[ée]pico|incr[ií]vel)|"
    r"olha (isso|s[oó])|n[aã]o acreditou|reagiu|ficou chocado|que isso|inacredit[aá]vel|surreal", re.I)


def is_generic_card(lines: list[str], display: str) -> bool:
    """Card que não conta nada do clipe ("Alanzoka no GTA 6", "momento insano"…)."""
    text = " ".join(lines).strip()
    rest = re.sub(re.escape(display), "", text, flags=re.I).strip(" ,.!-")
    if len(rest) < 10:
        return True
    return bool(GENERIC_RE.search(rest)) and len(rest.split()) < 7


# texto brega/fraco que não pode aparecer no card, no título nem na descrição
CRINGE_RE = re.compile(
    r"\bchat\b|\bsubs?\b|inscrit|doa[cç][aã]o|presente|foi [àa] loucura|explod|\breagiu|surtou|"
    r"o streamer|momento (insano|[ée]pico)|n[ãa]o acreditou", re.I)


def is_bad_card(lines: list[str], display: str) -> bool:
    return is_generic_card(lines, display) or bool(CRINGE_RE.search(" ".join(lines)))


# frase com cara de robô/jornal ("a situação sai do controle", "mostra como a jogabilidade…")
ROBOT_RE = re.compile(
    r"a situa[cç][aã]o|mostra como|deixa (todo mundo|todos)|em choque|jogabilidade|de forma inesperada|"
    r"\bo jogador\b|demonstra|evidencia|imprevis[ií]vel|ao conectar|protagoniz", re.I)


def _drop_cringe_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+|(?<=[\U0001F000-\U0001FAFF])\s+", text.strip())
    return " ".join(p for p in parts if p and not CRINGE_RE.search(p) and not ROBOT_RE.search(p)).strip()


def polish_title(title: str, card: list[str], display: str) -> str:
    """Título como a IA escreveu (cada um com a sua cara), só sem termo brega/robô e com a 1ª letra maiúscula.
    Antes todo título virava "GTA 6: … | Nome" — ficava tudo igual."""
    t = re.sub(r"\s+", " ", title or "").strip()
    if not t or CRINGE_RE.search(t) or ROBOT_RE.search(t):
        good_card = card and not is_bad_card(card, display)
        t = " ".join(card).strip().capitalize() if good_card else f"Olha o que o {display} aprontou no GTA 6 😂"
    first = t.split(" ", 1)[0]
    if first[:1].islower() and first[1:] == first[1:].lower():  # "quem deixou…" -> "Quem deixou…"; "xQc" fica
        t = t[:1].upper() + t[1:]
    return t[:100]

def _tag(s: str) -> str:
    return "#" + re.sub(r"[^0-9A-Za-zÀ-ÿ_]", "", s.lstrip("#"))


def merge_hashtags(llm_tags: list[str], streamer_display: str, base: list[str], limit: int = 10) -> list[str]:
    """Jogo + formato + streamer primeiro (o que mais traz busca), depois 3-4 do momento, depois as base."""
    forced = ["#GTA6", "#GTAVI", _tag(streamer_display), "#cortes"]
    specific = [_tag(x) for x in llm_tags if x and not CRINGE_RE.search(str(x))][:4]
    out, seen = [], set()
    for t in forced + specific + base:
        k = t.lower()
        if len(t) > 1 and k not in seen:
            seen.add(k)
            out.append(t)
    return out[:limit]


def fallback_texts(display: str, platform: str, live_title: str) -> PostTexts:
    return PostTexts(
        card=fit_card(f"{display} no GTA 6"),
        title=f"GTA 6: melhores momentos da live | {display}",
        description=(f"Corte da live de {display} jogando GTA 6."
                     + f"\n🎥 Live de {display} na {platform.capitalize()}"),
        hashtags=[],
    )


def polish(texts: PostTexts, display: str, platform: str) -> PostTexts:
    """Aplica o padrão profissional em textos de qualquer origem."""
    title = polish_title(texts.title, texts.card, display)
    desc = _drop_cringe_sentences(texts.description.split("\n🎥")[0])
    desc = (desc + "\n" if desc else "") + f"🎥 Live de {display} na {platform.capitalize()}"
    return PostTexts(texts.card, title, desc, texts.hashtags)


def make_texts(display: str, platform: str, transcript_pt: str, live_title: str,
               base_tags: list[str], model: str, chat_snippets: list[str] | None = None) -> PostTexts:
    chat = "\n".join((chat_snippets or [])[-25:])
    user = (
        f"Streamer: {display} (live na {platform}), jogando GTA 6.\n"
        f"Título da live: {live_title or '-'}\n"
        f"O que foi falado no clipe (já em PT-BR):\n\"\"\"{transcript_pt or '(sem fala, só reação)'}\"\"\"\n"
        f"Mensagens do chat no momento:\n{chat or '-'}\n\n"
        + CARD_RULES.replace("{d}", display) + "\n"
        + TITLE_RULES.replace("{d}", display) + "\n\n"
        'Responda JSON: {"card": ["linha 1", "linha 2"], "title": "...", "description": "...", '
        '"hashtags": ["#.."]}'
    )
    j = ai.chat_json(SYSTEM, user, model)
    fb = fallback_texts(display, platform, live_title)
    if not isinstance(j, dict):
        texts = fb
    else:
        card = fit_card(j.get("card") or "")
        card = card if card and not is_bad_card(card, display) else fb.card
        tags = j.get("hashtags") or []
        if isinstance(tags, str):
            tags = tags.split()
        texts = polish(PostTexts(card, str(j.get("title") or ""), str(j.get("description") or ""),
                                 [str(t) for t in tags]), display, platform)
    texts.hashtags = merge_hashtags(texts.hashtags, display, base_tags)
    return texts


def finish_texts(t: PostTexts, display: str, platform: str, base_tags: list[str]) -> PostTexts:
    """Padroniza textos vindos da IA: card ≤2 linhas e sem termo brega, título profissional, hashtags."""
    fb = fallback_texts(display, platform, "")
    card = fit_card(t.card)
    card = card if card and not is_bad_card(card, display) else fb.card
    out = polish(PostTexts(card, t.title, t.description, []), display, platform)
    out.hashtags = merge_hashtags([str(x) for x in t.hashtags], display, base_tags)
    return out


def refine_card(frames: list[bytes], display: str, transcript_pt: str, draft: list[str], title: str,
                chat_snippets: list[str] | None, model: str) -> list[str] | None:
    """Olha quadros do clipe (em ordem) + fala e reescreve o card para contar o que de fato aparece.
    Devolve None se a IA falhar ou só devolver algo genérico."""
    if not frames or not ai.any_key("vision"):
        return None
    chat = " | ".join((chat_snippets or [])[-15:])
    user = (
        f"Estas {len(frames)} imagens são quadros, em ordem, de um corte de ~1 minuto da live de {display} "
        "jogando GTA 6 (pode ter a webcam do streamer num canto e elementos da tela da live).\n"
        f"Fala no corte (PT-BR): \"\"\"{transcript_pt[:1500] or '(sem fala)'}\"\"\"\n"
        f"Chat: {chat or '-'}\n"
        f"Rascunho do card: {draft}  |  Título: {title or '-'}\n\n"
        "Junte o que você VÊ nas imagens com o que é FALADO e escreva o card definitivo, no tom informal de "
        "página de cortes (pode usar a fala do streamer entre aspas), nunca narrando em terceira pessoa. "
        "Cite detalhes concretos que aparecem (veículo, lugar, arma, polícia, explosão, perseguição...) "
        "só se estiverem nas imagens ou na fala. Não invente motivo nem intenção (\"para fugir\", "
        "\"pra se vingar\") que ninguém falou: descreva o que acontece, não o que você supõe.\n"
        + CARD_RULES.replace("{d}", display) + "\n"
        'Responda JSON: {"card": ["linha 1", "linha 2"]}'
    )
    j = ai.chat_json("Você é admin de perfil de cortes virais de GTA no Brasil: escreve informal, com gíria. "
                     "Responde só JSON.", user, model,
                     temperature=0.3, images=frames)
    if not isinstance(j, dict):
        return None
    card = fit_card(j.get("card") or "")
    if not card or is_bad_card(card, display):
        return None
    return card


TRANSLATE_SYSTEM = (
    "Você legenda cortes de live pra brasileiro no TikTok/Reels/Shorts. Traduz a fala pro português do Brasil "
    "do jeito que um brasileiro falaria AO VIVO, não como tradução de livro. Responde só JSON."
)
TRANSLATE_RULES = (
    "REGRAS:\n"
    "- Primeira pessoa, igual ao original: quem fala é o próprio streamer. \"I died\" = \"eu morri\", nunca "
    "\"ele morreu\". Nada de terceira pessoa, nada de narrar ou explicar.\n"
    "- Informal e falado: tá, tô, pra, cê, né, tipo. Gíria de live quando combinar: mano, véi, caraca, "
    "nem a pau, que isso, tá ligado, bora, mó. Palavrão vira o palavrão brasileiro equivalente "
    "(\"what the fuck\" = \"que porra é essa\", \"bro\" = \"mano\", \"no way\" = \"nem fodendo\"/\"não acredito\").\n"
    "- Curto e direto como se fala; pode cortar repetição e \"uh\", \"like\". Não traduz nome de pessoa, "
    "lugar ou carro do jogo.\n"
    "- Mesmo número de itens e mesmo \"i\" de cada um.\n"
)


def _even_words(seg: Seg, text: str) -> list:
    """Palavras com tempo espalhado no trecho (pro karaokê mostrar a frase traduzida)."""
    from .ai import Word
    parts = text.split()
    if not parts:
        return []
    step = (seg.end - seg.start) / len(parts)
    return [Word(seg.start + i * step, seg.start + (i + 1) * step, w) for i, w in enumerate(parts)]


def _ask_translation(items: list[tuple[int, str]], lang: str, model: str) -> dict[int, str]:
    payload = [{"i": i, "texto": txt} for i, txt in items]
    src = LANG_PT.get(lang, "inglês/espanhol") if lang else "inglês/espanhol"
    user = (f"Traduza do {src} pro português do Brasil.\n" + TRANSLATE_RULES +
            'Responda {"itens": [{"i": 0, "texto": "..."}]}.\n' + json.dumps(payload, ensure_ascii=False))
    j = ai.chat_json(TRANSLATE_SYSTEM, user, model, temperature=0.4)
    try:
        return {int(x["i"]): str(x["texto"]).strip() for x in j["itens"] if str(x.get("texto", "")).strip()}
    except (TypeError, KeyError, ValueError, AttributeError):
        return {}


def translate_transcript(t: Transcript, model: str) -> Transcript:
    """Legenda em PT-BR natural, mantendo os tempos.
    - Fala em outro idioma (Kai Cenat, IShowSpeed…): traduz tudo.
    - Fala em português: traduz só os trechos em inglês/espanhol (NPC do jogo, gringo na call).
    Se a IA devolver faltando trecho, pede de novo só o que faltou; se falhar de vez, devolve o original."""
    if not t.segments:
        return t
    is_pt = t.lang.startswith("pt")
    todo = [(i, s.text) for i, s in enumerate(t.segments) if not is_pt or is_foreign(s.text)]
    if not todo:
        return t
    got = _ask_translation(todo, "" if is_pt else t.lang, model)
    missing = [(i, txt) for i, txt in todo if i not in got]
    if missing:
        got.update(_ask_translation(missing, "" if is_pt else t.lang, model))
    if not got or (not is_pt and len(got) < len(todo)):
        return t
    segs = []
    for i, s in enumerate(t.segments):
        if i in got:
            segs.append(Seg(s.start, s.end, got[i], _even_words(s, got[i]) if is_pt else []))
        else:
            segs.append(s)
    return Transcript("pt", segs)