"""IA grátis em rodízio: legenda (Groq Whisper → Whisper local) e texto/visão (Groq, Cerebras, Gemini)."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .recorder import NO_WINDOW, ffmpeg_bin

log = logging.getLogger(__name__)
GROQ_URL = "https://api.groq.com/openai/v1"
LANG_NAMES = {"english": "en", "portuguese": "pt", "spanish": "es", "german": "de", "russian": "ru",
              "french": "fr", "italian": "it"}


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Seg:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    lang: str
    segments: list[Seg]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def to_json(self) -> dict:
        return {"lang": self.lang, "segments": [
            {"start": s.start, "end": s.end, "text": s.text,
             "words": [vars(w) for w in s.words]} for s in self.segments]}

    @classmethod
    def from_json(cls, d: dict) -> "Transcript":
        return cls(d.get("lang", ""), [Seg(s["start"], s["end"], s["text"],
                                           [Word(**w) for w in s.get("words", [])])
                                       for s in d.get("segments", [])])


def groq_key() -> str | None:
    k = os.environ.get("GROQ_API_KEY", "").strip()
    return k or None


def extract_audio(video: Path, out: Path) -> Path:
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
                    "-ar", "16000", "-b:a", "48k", str(out)], check=True, creationflags=NO_WINDOW)
    return out


# ---------------- rodízio grátis de IA ----------------
# Cada provedor tem cota grátis por minuto/dia POR MODELO. Quando um par (chave, modelo) bate o limite,
# ele fica de lado pelo tempo que o serviço pedir e o próximo par é usado na hora. Modelo aposentado sai sozinho.
@dataclass(frozen=True)
class Provider:
    name: str
    base: str
    env: str
    text: tuple[str, ...]
    vision: tuple[str, ...] = ()
    whisper: tuple[str, ...] = ()
    json_mode: bool = True
    signup: str = ""


PROVIDERS: tuple[Provider, ...] = (
    Provider("groq", GROQ_URL, "GROQ_API_KEY",
             text=("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"),
             vision=("qwen/qwen3.8-27b", "meta-llama/llama-4-maverick-17b-128e-instruct",
                     "meta-llama/llama-4-scout-17b-16e-instruct"),
             whisper=("whisper-large-v3", "whisper-large-v3-turbo"),
             signup="https://console.groq.com/keys"),
    Provider("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
             text=("gpt-oss-120b", "qwen-3-235b-a22b-instruct-2507", "llama-3.3-70b", "qwen-3-32b", "llama3.1-8b"),
             signup="https://cloud.cerebras.ai"),
    Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
             text=("gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash", "gemini-2.5-flash-lite"),
             vision=("gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"),
             json_mode=False, signup="https://aistudio.google.com/apikey"),
)
MAX_WAIT_S = 25  # se tudo estiver no limite por pouco tempo, espera até isso; mais que isso, desiste

_cool: dict[tuple[str, str, str], float] = {}     # (provedor, chave, modelo) -> livre a partir de
_dead: set[tuple[str, str]] = set()               # (provedor, modelo) aposentado / sem acesso
_bad_keys: dict[tuple[str, str], float] = {}      # chave recusada (401/403) -> até quando ignorar
_models_cache: dict[tuple[str, str], tuple[float, set[str] | None]] = {}
_state_lock = threading.Lock()


def reset_state() -> None:
    with _state_lock:
        _cool.clear(), _dead.clear(), _bad_keys.clear(), _models_cache.clear()


def keys_for(p: Provider) -> list[str]:
    """Aceita várias chaves no mesmo campo (vírgula, espaço ou uma por linha)."""
    return [k for k in re.split(r"[\s,;]+", os.environ.get(p.env, "")) if k]


def any_key(kind: str = "text") -> bool:
    return any(keys_for(p) and getattr(p, kind) for p in PROVIDERS)


def parse_wait(v: str | None, default: float = 10.0) -> float:
    """'7', '577ms', '1m26.4s', '2h3m' -> segundos."""
    if not v:
        return default
    v = v.strip()
    try:
        return float(v)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", v):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else default


def _models(p: Provider, key: str) -> set[str] | None:
    """Modelos que a chave enxerga (cache de 1 h). None = não deu pra saber (tenta mesmo assim)."""
    now = time.time()
    hit = _models_cache.get((p.name, key))
    if hit and now - hit[0] < 3600:
        return hit[1]
    got: set[str] | None = None
    try:
        r = httpx.get(f"{p.base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        if r.status_code == 200:
            got = {str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data", [])}
        elif r.status_code in (401, 403):
            _bad_keys[(p.name, key)] = now + 3600
    except (httpx.HTTPError, ValueError, AttributeError):
        pass
    _models_cache[(p.name, key)] = (now, got)
    return got


def _candidates(kind: str, preferred: str | None = None):
    now = time.time()
    for p in PROVIDERS:
        prefs = list(getattr(p, kind))
        if preferred and preferred in prefs:
            prefs.remove(preferred)
            prefs.insert(0, preferred)
        if not prefs:
            continue
        for key in keys_for(p):
            if _bad_keys.get((p.name, key), 0) > now:
                continue
            avail = _models(p, key)
            for m in prefs:
                if (p.name, m) in _dead or (avail is not None and m not in avail):
                    continue
                if _cool.get((p.name, key, m), 0) > now:
                    continue
                yield p, key, m


def _soonest_free(kind: str) -> float | None:
    names = {p.name for p in PROVIDERS if getattr(p, kind)}
    waits = [t for (pn, _, _), t in _cool.items() if pn in names]
    return min(waits) if waits else None


def _error_info(r: httpx.Response) -> tuple[str, str]:
    """(código, mensagem) do erro, em minúsculas, venha ele como objeto, LISTA (Gemini) ou texto."""
    try:
        body = r.json()
    except ValueError:
        return "", r.text[:300].lower()
    if isinstance(body, list):
        body = next((x for x in body if isinstance(x, dict)), {})
    if not isinstance(body, dict):
        return "", ""
    err = body.get("error", body)
    if isinstance(err, list):
        err = next((x for x in err if isinstance(x, dict)), {})
    if isinstance(err, str):
        return "", err.lower()
    if not isinstance(err, dict):
        return "", ""
    return str(err.get("code", "") or "").lower(), str(err.get("message", "") or "").lower()


def _model_gone(r: httpx.Response) -> bool:
    if r.status_code not in (400, 404):
        return False
    code, msg = _error_info(r)
    if "content must be a string" in msg or "image" in msg and "support" in msg:
        return True  # mandou imagem pra um modelo sem visão
    return "model" in code or ("model" in msg and any(w in msg for w in (
        "decommission", "not exist", "not found", "does not support", "no access", "not supported")))


def _call(kind: str, path: str, preferred: str | None, make_kw) -> httpx.Response | None:
    """Tenta (provedor, chave, modelo) em rodízio até um responder 200."""
    deadline = time.time() + MAX_WAIT_S + 5
    while True:
        progressed = False
        for p, key, m in _candidates(kind, preferred):
            progressed = True
            for attempt in range(2):  # erro de rede/5xx: 1 nova tentativa
                try:
                    r = httpx.post(f"{p.base}{path}", headers={"Authorization": f"Bearer {key}"}, timeout=90,
                                   **make_kw(p, m))
                except httpx.HTTPError as e:
                    log.warning("%s falhou (%s): %s", p.name, m, e)
                    time.sleep(1.5)
                    continue
                if r.status_code == 200:
                    return r
                if r.status_code == 429:
                    wait = parse_wait(r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset-tokens")
                                      or r.headers.get("x-ratelimit-reset-requests"))
                    with _state_lock:
                        _cool[(p.name, key, m)] = time.time() + max(wait, 1.0)
                    log.info("%s %s no limite grátis por %.0fs — trocando de modelo", p.name, m, wait)
                    break
                if r.status_code >= 500:
                    time.sleep(1.5)
                    continue
                try:
                    gone = _model_gone(r)
                except Exception:  # resposta de erro num formato inesperado: só pula pra próxima IA
                    log.warning("%s (%s) respondeu %s num formato inesperado", p.name, m, r.status_code)
                    break
                if gone:
                    with _state_lock:
                        _dead.add((p.name, m))
                    log.warning("modelo %s indisponível no %s — usando outro", m, p.name)
                    break
                if r.status_code in (401, 403):
                    with _state_lock:
                        _bad_keys[(p.name, key)] = time.time() + 3600
                    log.warning("%s recusou a chave (%s)", p.name, r.status_code)
                    break
                log.warning("%s recusou o pedido (%s): %s", p.name, r.status_code, r.text[:200])
                break
        # nada respondeu: se algo sai do limite logo, espera; senão desiste (o pipeline tem plano B)
        soon = _soonest_free(kind)
        now = time.time()
        if soon is None or soon - now > MAX_WAIT_S or now >= deadline:
            if not progressed and soon is not None:
                log.warning("toda a IA grátis (%s) está no limite agora — usando o plano B", kind)
            return None
        time.sleep(max(0.5, soon - now))


def available_models() -> list[str]:
    """Modelos que alguma chave enxerga (vazio = nenhuma chave funcionando)."""
    out: list[str] = []
    for p in PROVIDERS:
        for key in keys_for(p):
            _models_cache.pop((p.name, key), None)
            out += sorted(_models(p, key) or [])
    return out


def check_providers() -> dict[str, str]:
    """Pra tela de Configurações: situação de cada provedor."""
    res = {}
    for p in PROVIDERS:
        ks = keys_for(p)
        if not ks:
            res[p.name] = "sem chave"
            continue
        ok = 0
        for key in ks:
            _models_cache.pop((p.name, key), None)
            ok += _models(p, key) is not None
        res[p.name] = f"ok ({ok} chave{'s' if ok > 1 else ''})" if ok else "chave recusada"
    return res


# ---------------- transcrição ----------------
def _is_hallucination(s: dict) -> bool:
    """Whisper "inventa" frases em música/barulho: descarta trechos com cara de ruído."""
    if float(s.get("no_speech_prob", 0) or 0) > 0.6:
        return True
    if float(s.get("avg_logprob", 0) or 0) < -1.2:
        return True
    return float(s.get("compression_ratio", 0) or 0) > 2.6


def _words_into_segments(segs: list[dict], words: list[dict]) -> list[Seg]:
    segs = [s for s in segs if not _is_hallucination(s)]
    out = [Seg(float(s["start"]), float(s["end"]), s.get("text", "").strip()) for s in segs]
    for w in words:
        ws, we, wt = float(w["start"]), float(w["end"]), str(w.get("word", "")).strip()
        if not wt:
            continue
        mid = (ws + we) / 2
        target = next((s for s in out if s.start <= mid <= s.end), None) or \
            next((s for s in out if s.start - 0.3 <= mid <= s.end + 0.3), None)
        if target is not None:  # palavra fora de trecho válido = ruído descartado
            target.words.append(Word(ws, we, wt))
    return [s for s in out if s.text]


def transcribe_groq(audio: Path, lang_hint: str | None, model: str, prompt: str | None = None) -> Transcript | None:
    data = {"model": model, "response_format": "verbose_json", "temperature": "0",
            "timestamp_granularities[]": ["word", "segment"]}
    if lang_hint:
        data["language"] = lang_hint[:2]
    if prompt:
        data["prompt"] = prompt[:800]
    # bytes (não o arquivo aberto): nas tentativas após um 429 o arquivo já estaria no fim → upload vazio
    payload = audio.read_bytes()
    r = _call("whisper", "/audio/transcriptions", model,
              lambda p, m: {"data": {**data, "model": m}, "files": {"file": (audio.name, payload, "audio/mpeg")}})
    if r is None:  # cota de legenda acabou: transcribe() cai no Whisper local
        return None
    j = r.json()
    lang = str(j.get("language", lang_hint or "")).lower()
    lang = LANG_NAMES.get(lang, lang[:2])
    return Transcript(lang, _words_into_segments(j.get("segments") or [], j.get("words") or []))


_local_model = None


def transcribe_local(audio: Path, lang_hint: str | None, model: str = "small", prompt: str | None = None) -> Transcript | None:
    global _local_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("Whisper local não instalado (pip install faster-whisper) — clipe sem legenda")
        return None
    try:
        if _local_model is None:
            try:
                _local_model = WhisperModel(model, device="cuda", compute_type="int8_float16")
            except Exception:
                _local_model = WhisperModel(model, device="cpu", compute_type="int8")
        segs, info = _local_model.transcribe(str(audio), language=(lang_hint or None) and lang_hint[:2],
                                             word_timestamps=True, vad_filter=True,
                                             initial_prompt=prompt)
        out = [Seg(s.start, s.end, s.text.strip(),
                   [Word(w.start, w.end, w.word.strip()) for w in (s.words or [])]) for s in segs]
        return Transcript(info.language, [s for s in out if s.text])
    except Exception:
        log.exception("Whisper local falhou")
        return None


def whisper_prompt(display: str, lang: str) -> str:
    """Contexto pro Whisper acertar nomes e gírias."""
    if lang.startswith("pt"):
        return (f"Live do {display} jogando GTA 6 (GTA VI). Vice City, Leonida, Lucia, Jason, Rockstar. "
                "Gírias: mano, véi, caraca, KKKKK, meu Deus, que isso, tá maluco, chat.")
    return f"{display} livestream playing GTA 6 (GTA VI). Vice City, Leonida, Lucia, Jason, Rockstar, chat."


def transcribe(video: Path, lang_hint: str | None, groq_model: str, local_model: str,
               prompt: str | None = None) -> Transcript:
    audio = extract_audio(video, video.with_name("audio.mp3"))
    t = transcribe_groq(audio, lang_hint, groq_model, prompt) or transcribe_local(audio, lang_hint, local_model, prompt)
    return t or Transcript(lang_hint or "", [])


# ---------------- texto ----------------
def _parse_json(content: str) -> dict | None:
    content = (content or "").strip()
    if "</think>" in content:  # alguns modelos "pensam em voz alta" antes do JSON
        content = content.split("</think>", 1)[1]
    try:
        return json.loads(content)
    except ValueError:
        a, b = content.find("{"), content.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(content[a:b + 1])
            except ValueError:
                pass
    return None


def chat_json(system: str, user: str, model: str, temperature: float = 0.7,
              images: list[bytes] | None = None) -> dict | None:
    """Pergunta à IA grátis (rodízio Groq → Cerebras → Gemini) e devolve o JSON da resposta.
    `images` (JPEG) vão para um modelo com visão. None = nenhuma IA respondeu (o pipeline tem plano B)."""
    content: str | list = user
    if images:
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
            for b in images]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": content}]

    def body(p: Provider, m: str) -> dict:
        b = {"model": m, "temperature": temperature, "messages": msgs}
        if p.json_mode:
            b["response_format"] = {"type": "json_object"}
        return {"json": b}

    r = _call("vision" if images else "text", "/chat/completions", model, body)
    if r is None:
        return None
    try:
        j = _parse_json(r.json()["choices"][0]["message"]["content"])
    except (KeyError, IndexError, ValueError, TypeError):
        j = None
    if not isinstance(j, dict):
        log.warning("Resposta da IA não veio em JSON válido")
        return None
    return j
