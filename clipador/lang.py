"""Palpite rápido de idioma por palavras comuns (sem IA): título de live e trechos de fala."""
from __future__ import annotations

import re

WORDS = {
    "pt": {"ao", "vivo", "até", "ate", "jogando", "dia", "zerar", "hoje", "com", "aventura", "continua", "história",
           "todo", "primeira", "agora", "você", "cê", "não", "nao", "é", "continuando", "jogo", "que", "eu", "tá",
           "ta", "mano", "cara", "pra", "isso", "aqui", "vai", "meu", "deus", "né", "olha", "caralho", "ele", "ela",
           "tô", "to", "uma", "um", "de", "do", "da", "no", "na", "os", "as", "mas", "porra", "nossa", "gente",
           "também", "muito", "coisa", "quero", "vamo", "vamos", "galera", "tem", "foi", "esse", "essa"},
    "es": {"en", "directo", "día", "jugando", "hoy", "con", "seguimos", "el", "los", "las", "historia", "desde",
           "primera", "ahora", "sí", "vamos", "juego", "qué", "pero", "muy", "estoy", "tengo", "hermano", "esto",
           "eso", "aquí", "nada", "también", "puta", "madre", "joder", "tío", "wey", "güey"},
    "en": {"playing", "the", "first", "time", "live", "stream", "day", "full", "game", "walkthrough", "is", "here",
           "get", "for", "right", "now", "with", "part", "actually", "gameplay", "i", "you", "to", "and", "what",
           "oh", "my", "god", "yes", "bro", "that", "this", "it", "im", "i'm", "dont", "don't", "wanna", "gonna",
           "go", "come", "let", "we", "he", "she", "they", "are", "was", "got", "just", "like", "man", "fuck",
           "fucking", "shit", "yo", "hey", "hi", "talk", "hate", "love", "why", "where", "who", "how", "chat",
           "dude", "bruh", "what's", "can't", "cant", "did", "didn't", "have", "your", "me", "him", "her", "there"},
}


def scores(text: str) -> dict[str, int]:
    words = re.findall(r"[a-zà-úñç']+", (text or "").casefold())
    return {lang: sum(w in vocab for w in words) for lang, vocab in WORDS.items()}


def guess_lang(text: str) -> str:
    """'pt', 'es', 'en' ou '' (não sei)."""
    s = scores(text)
    best = max(s, key=s.get)
    ties = [k for k, v in s.items() if v == s[best]]
    return best if s[best] > 0 and len(ties) == 1 else ""


def is_foreign(text: str) -> bool:
    """Trecho em inglês/espanhol no meio de uma fala em português (NPC do jogo, gringo na call…)."""
    s = scores(text)
    foreign = max(s["en"], s["es"])
    return foreign >= 2 and foreign > s["pt"]
