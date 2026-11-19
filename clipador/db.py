"""Banco SQLite local dos clipes."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_lock = threading.Lock()
_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  streamer TEXT NOT NULL,
  display TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  workdir TEXT NOT NULL,
  video_path TEXT NOT NULL,
  cover_path TEXT NOT NULL,
  card TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  hashtags TEXT NOT NULL,
  transcript TEXT NOT NULL DEFAULT '',
  lang TEXT NOT NULL DEFAULT '',
  duration REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pronto',
  posted INTEGER NOT NULL DEFAULT 0,
  deleted_at REAL NOT NULL DEFAULT 0
);
"""


@dataclass
class Clip:
    streamer: str
    display: str
    workdir: str
    video_path: str
    cover_path: str
    card: list[str]
    title: str
    description: str
    hashtags: list[str]
    transcript: str = ""
    lang: str = ""
    duration: float = 0.0
    platform: str = ""
    status: str = "pronto"
    posted: bool = False
    created_at: float = field(default_factory=time.time)
    deleted_at: float = 0.0  # > 0 = está na Lixeira (arquivos continuam no disco)
    id: int | None = None

    def to_json(self) -> dict:
        return asdict(self)


def init(path: Path | str) -> None:
    global _path
    _path = Path(path)
    _path.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(clips)")}
        if "deleted_at" not in cols:  # banco de versão antiga: ganha a Lixeira sem perder nada
            c.execute("ALTER TABLE clips ADD COLUMN deleted_at REAL NOT NULL DEFAULT 0")


def _conn() -> sqlite3.Connection:
    if _path is None:
        raise RuntimeError("db.init() não foi chamado")
    c = sqlite3.connect(_path, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _row(r: sqlite3.Row) -> Clip:
    d = dict(r)
    d["card"] = json.loads(d["card"])
    d["hashtags"] = json.loads(d["hashtags"])
    d["posted"] = bool(d["posted"])
    return Clip(**d)


def add_clip(clip: Clip) -> int:
    d = clip.to_json()
    d.pop("id")
    d["card"] = json.dumps(d["card"], ensure_ascii=False)
    d["hashtags"] = json.dumps(d["hashtags"], ensure_ascii=False)
    d["posted"] = int(d["posted"])
    cols = ",".join(d)
    qs = ",".join("?" for _ in d)
    with _lock, _conn() as c:
        cur = c.execute(f"INSERT INTO clips ({cols}) VALUES ({qs})", list(d.values()))
        clip.id = cur.lastrowid
        return clip.id


def get_clip(clip_id: int) -> Clip | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()
    return _row(r) if r else None


def list_clips(posted: bool | None = None, streamer: str | None = None, trash: bool = False) -> list[Clip]:
    q, args = "SELECT * FROM clips WHERE " + ("deleted_at > 0" if trash else "deleted_at = 0"), []
    if posted is not None:
        q += " AND posted=?"
        args.append(int(posted))
    if streamer:
        q += " AND streamer=?"
        args.append(streamer)
    q += " ORDER BY created_at DESC"
    with _conn() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def update_clip(clip_id: int, **fields) -> None:
    if not fields:
        return
    for k in ("card", "hashtags"):
        if k in fields:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    if "posted" in fields:
        fields["posted"] = int(fields["posted"])
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE clips SET {sets} WHERE id=?", [*fields.values(), clip_id])


def mark_posted(clip_id: int, posted: bool = True) -> None:
    update_clip(clip_id, posted=posted)


def trash_clip(clip_id: int) -> None:
    """Manda pra Lixeira: some da lista, mas o vídeo e os textos continuam (dá pra restaurar)."""
    update_clip(clip_id, deleted_at=time.time())


def restore_clip(clip_id: int) -> None:
    update_clip(clip_id, deleted_at=0)


def known_workdirs() -> set[str]:
    with _conn() as c:
        return {r[0] for r in c.execute("SELECT workdir FROM clips")}


def delete_clip(clip_id: int) -> None:
    """Remove o registro de vez (só pra quem já está na Lixeira e o usuário mandou apagar)."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM clips WHERE id=?", (clip_id,))
