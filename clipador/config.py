"""Configuração: settings + streamers (streamers.yaml) + webcams salvas pelo painel."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from .frozen import app_dirs

# APP_DIR: arquivos do usuário (streamers.yaml, .env, data/) — ao lado do .exe quando empacotado
# RES_DIR: recursos que vêm junto (assets/fontes, modelo do rosto)
APP_DIR, RES_DIR = app_dirs()
DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
Platform = Literal["twitch", "kick", "youtube"]
PLATFORMS = ("twitch", "kick", "youtube")


@dataclass
class Channel:
    platform: Platform
    channel: str

    @property
    def url(self) -> str:
        if self.platform == "twitch":
            return f"https://www.twitch.tv/{self.channel}"
        if self.platform == "kick":
            return f"https://kick.com/{self.channel}"
        if re.fullmatch(r"UC[\w-]{22}", self.channel):  # canal sem @: pelo ID
            return f"https://www.youtube.com/channel/{self.channel}/live"
        handle = self.channel if self.channel.startswith("@") else f"@{self.channel}"
        return f"https://www.youtube.com/{handle}/live"


@dataclass
class Streamer:
    name: str
    display: str
    lang: str
    channels: list[Channel]
    enabled: bool = True
    webcam: tuple[int, int, int, int] | None = None  # x, y, w, h no frame 1280x720
    auto: bool = False  # achado sozinho (live de GTA muito vista), não está na lista

    @property
    def is_pt(self) -> bool:
        return self.lang.lower().startswith("pt")


QUALITY_HD = "1080p60,1080p,936p60,900p60,720p60,720p,best"   # ~6-8 Mbps por live
QUALITY_MAX = "best"                                           # a maior que a live tiver (YouTube: até 1440p/4K)
QUALITY_LIGHT = "720p60,720p,480p,best"                        # ~3-4 Mbps por live (webcam mais borrada)


@dataclass
class Settings:
    pre_s: float = 43
    post_s: float = 15
    max_clip_s: float = 90          # Shorts/TikTok aceitam mais de 1 min: a IA só passa de ideal_clip_s se a história pedir
    ideal_clip_s: float = 60        # duração que a IA mira
    min_clip_s: float = 45
    analysis_before_s: float = 90   # quanto a IA olha antes do pico
    analysis_after_s: float = 25    # e depois
    min_score: float = 7            # nota mínima (0-10) que a IA precisa dar pro clipe ser feito
    cooldown_s: float = 120
    max_per_hour: int = 6
    max_live: int = 12
    hype_threshold: float = 3.0
    require_gta: bool = True
    handle: str = "@seuperfil"
    accent: str = "#FF2E63"
    card_seconds: float = 4.0  # o card some depois disso (0 = fica o vídeo todo)
    auto_discover: bool = True       # acha sozinho as lives de GTA mais vistas (Twitch, Kick, YouTube)
    discover_min_viewers: int = 3000 # só quem tem pelo menos isso de gente assistindo
    discover_max: int = 8            # quantas lives achadas sozinho no máximo
    discover_langs: list[str] = field(default_factory=lambda: ["pt", "en", "es"])  # idiomas dos achados
    zoom: str = "aberto"       # quanto do jogo aparece: aberto (mais jogo), medio, fechado (jogo enche a tela)
    auto_webcam: bool = True   # acha a webcam sozinho quando ela não foi marcada à mão
    auto_clip: bool = True     # cortes automáticos ligados (o botão Parar/Ligar do painel)
    quality: str = QUALITY_HD  # gravação da live (HD = webcam nítida no vertical)
    whisper_local_model: str = "small"
    groq_text_model: str = "openai/gpt-oss-120b"     # se o Groq aposentar, ai.py escolhe outro sozinho
    groq_vision_model: str = "qwen/qwen3.8-27b"      # olha quadros do clipe para escrever o card
    groq_whisper_model: str = "whisper-large-v3"
    port: int = 8765
    base_hashtags: list[str] = field(default_factory=list)
    data_dir: Path = APP_DIR / "data"

    def __post_init__(self) -> None:
        if self.max_clip_s > 90:
            raise ValueError("max_clip_s não pode passar de 90 segundos")
        if self.ideal_clip_s > self.max_clip_s:
            raise ValueError("ideal_clip_s não pode ser maior que max_clip_s")
        if self.min_clip_s > self.max_clip_s:
            raise ValueError("min_clip_s não pode ser maior que max_clip_s")
        if self.pre_s + self.post_s <= 0:
            raise ValueError("pre_s + post_s precisa ser > 0")
        self.data_dir = Path(self.data_dir)
        if not self.data_dir.is_absolute():
            self.data_dir = APP_DIR / self.data_dir


def user_dir() -> Path:
    """Pasta fora do Clipador (%APPDATA%/ClipadorGTA6) para o que não pode sumir numa atualização:
    chave do Groq, ajustes do painel e webcams marcadas."""
    base = os.environ.get("CLIPADOR_HOME")
    if base:
        d = Path(base)
    elif os.environ.get("APPDATA"):
        d = Path(os.environ["APPDATA"]) / "ClipadorGTA6"
    else:
        d = Path.home() / ".clipador"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            out[k.strip()] = v
    return out


def save_user_env(key: str, value: str) -> None:
    """Grava/troca KEY=valor no .env da pasta do usuário e já aplica no processo."""
    path = user_dir() / ".env"
    lines = [l for l in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
             if not l.strip().startswith(f"{key}=")]
    value = value.strip()
    if value:
        lines.append(f"{key}={value}")
        os.environ[key] = value
    else:
        os.environ.pop(key, None)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_env(path: Path | None = None) -> None:
    """Carrega o .env do Clipador e o da pasta do usuário (sem sobrescrever o ambiente).
    O que estiver no .env do Clipador é copiado para a pasta do usuário, então uma
    versão nova descompactada com .env vazio continua com a chave."""
    project = _read_env(path or APP_DIR / ".env")
    saved = _read_env(user_dir() / ".env")
    for k, v in project.items():
        if v not in saved.get(k, ""):  # já salva (talvez junto com outras chaves do painel): não sobrescreve
            save_user_env(k, v)
            saved[k] = v
    for k, v in {**saved, **project}.items():
        os.environ.setdefault(k, v)


def user_settings_path() -> Path:
    return user_dir() / "config.yaml"


def load_user_settings() -> dict:
    p = user_settings_path()
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def save_user_settings(changes: dict) -> dict:
    """Ajustes feitos no painel. Só aceita campos que existem em Settings."""
    allowed = {f for f in Settings.__dataclass_fields__ if f not in ("data_dir", "base_hashtags")}
    data = load_user_settings()
    data.update({k: v for k, v in changes.items() if k in allowed})
    user_settings_path().write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return data


def save_streamers_enabled(changes: dict[str, bool]) -> dict[str, bool]:
    """Quais streamers clipar (escolha do painel). Vale por cima do `enabled` do streamers.yaml."""
    data = load_user_settings()
    cur = dict(data.get("streamers_enabled") or {})
    cur.update({str(k): bool(v) for k, v in changes.items()})
    data["streamers_enabled"] = cur
    user_settings_path().write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return cur


def webcams_path(settings: Settings | None = None) -> Path:
    return user_dir() / "webcams.json"


def load_webcams(settings: Settings) -> dict[str, list[int] | None]:
    data: dict[str, list[int] | None] = {}
    legacy = settings.data_dir / "webcams.json"  # versões antigas salvavam dentro de data/
    for p in (legacy, webcams_path()):
        if p.exists():
            try:
                data.update(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                pass
    return data


def save_webcam(settings: Settings, name: str, rect: tuple[int, int, int, int] | None) -> None:
    data = load_webcams(settings)
    data[name] = list(rect) if rect else None
    webcams_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_hashtags(path: Path | None = None) -> list[str]:
    path = path or APP_DIR / "hashtags.txt"
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        path = DEFAULTS_DIR / "hashtags.txt"
    tags = []
    for line in path.read_text(encoding="utf-8").splitlines():
        for t in line.split():
            if t.startswith("#") and len(t) > 1:
                tags.append(t)
    return tags


def extra_streamers_path() -> Path:
    return user_dir() / "streamers_extra.yaml"


def save_extra_streamer(s: "Streamer") -> None:
    """Streamer que entrou pela VOD (não estava na lista): fica salvo na pasta do usuário e passa a valer
    como os da lista (inclusive ao vivo)."""
    p = extra_streamers_path()
    data = (yaml.safe_load(p.read_text(encoding="utf-8")) or []) if p.exists() else []
    data = [d for d in data if isinstance(d, dict) and d.get("name") != s.name]
    data.append({"name": s.name, "display": s.display, "lang": s.lang,
                 "channels": [{"platform": c.platform, "channel": c.channel} for c in s.channels]})
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _load_extra_streamers() -> list[dict]:
    p = extra_streamers_path()
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except yaml.YAMLError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("name") and d.get("channels")]


def load_config(path: Path | str | None = None) -> tuple[Settings, list[Streamer]]:
    path = Path(path) if path else APP_DIR / "streamers.yaml"
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    raw = yaml.safe_load(text) or {}
    if not raw.get("streamers"):
        # arquivo faltando/vazio (o zip veio sem ele): usa a lista padrão e recria pra editar
        default = (DEFAULTS_DIR / "streamers.yaml").read_text(encoding="utf-8")
        raw = {**(yaml.safe_load(default) or {}), **{k: v for k, v in raw.items() if v}}
        if not text.strip():
            try:
                path.write_text(default, encoding="utf-8")
            except OSError:
                pass
    fields = Settings.__dataclass_fields__
    user = load_user_settings()
    chosen = user.get("streamers_enabled") or {}
    merged = {**(raw.get("settings") or {}), **user}
    settings = Settings(**{k: v for k, v in merged.items() if k in fields})
    if not settings.base_hashtags:
        settings.base_hashtags = load_hashtags(path.parent / "hashtags.txt")
    cams = load_webcams(settings)
    streamers: list[Streamer] = []
    listed = list(raw.get("streamers") or [])
    names = {s.get("name") for s in listed}
    listed += [d for d in _load_extra_streamers() if d["name"] not in names]
    for s in listed:
        chans = []
        for c in s.get("channels") or []:
            if c["platform"] not in PLATFORMS:
                raise ValueError(f"plataforma inválida: {c['platform']}")
            chans.append(Channel(c["platform"], str(c["channel"])))
        name = s["name"]
        cam = cams.get(name, s.get("webcam"))
        streamers.append(Streamer(
            name=name,
            display=s.get("display", name),
            lang=s.get("lang", "pt"),
            channels=chans,
            enabled=bool(chosen.get(name, s.get("enabled", True))),
            webcam=tuple(cam) if cam else None,
        ))
    return settings, streamers
