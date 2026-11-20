"""Painel local: clipes prontos, lives gravando, configurador de webcam, VOD e teste."""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import db
from .. import twitchclips
from ..export import export_clip, resolve_dirs
from ..recover import GONE_MARK
from ..ai import PROVIDERS, any_key, check_providers, groq_key
from ..ai import reset_state as reset_ai_state
from ..config import (QUALITY_HD, QUALITY_LIGHT, QUALITY_MAX, Settings, Streamer, extra_streamer_names,
                      load_config, remove_extra_streamer, save_extra_streamer, save_streamers_enabled, save_user_env,
                      save_user_settings, save_webcam, user_dir)
from ..pipeline import process_candidate, rerender_card
from ..recorder import frame_from_file, probe, snapshot
from ..render import FONTS

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class PostedIn(BaseModel):
    posted: bool = True


class EditIn(BaseModel):
    title: str | None = None
    description: str | None = None
    hashtags: list[str] | None = None


class CardIn(BaseModel):
    lines: list[str]


class WebcamIn(BaseModel):
    rect: list[int] | None = None


class IdsIn(BaseModel):
    ids: list[int]
    posted: bool = True


class AddStreamerIn(BaseModel):
    display: str = Field(min_length=1, max_length=60)
    links: str = Field(min_length=1, max_length=1000)  # um ou mais links de canal (Twitch, Kick, YouTube)
    lang: Literal["pt", "en", "es"] = "pt"


class VodIn(BaseModel):
    url: str
    streamer: str = ""  # vazio: descobre sozinho pelo link de quem é a VOD
    top_n: int = 6


class AutoIn(BaseModel):
    on: bool


class EnabledIn(BaseModel):
    enabled: dict[str, bool]


class SettingsIn(BaseModel):
    handle: str | None = Field(None, max_length=40)
    min_score: float | None = Field(None, ge=0, le=10)
    card_seconds: float | None = Field(None, ge=0, le=60)
    require_gta: bool | None = None
    zoom: Literal["aberto", "medio", "fechado"] | None = None
    quality: Literal["max", "hd", "leve"] | None = None
    auto_webcam: bool | None = None
    auto_discover: bool | None = None
    discover_min_viewers: int | None = Field(None, ge=0, le=1_000_000)
    save_video_dir: str | None = Field(None, max_length=500)
    save_cover_dir: str | None = Field(None, max_length=500)
    groq_key: str | None = Field(None, max_length=2000)
    cerebras_key: str | None = Field(None, max_length=2000)
    gemini_key: str | None = Field(None, max_length=2000)
    twitch_client_id: str | None = Field(None, max_length=200)
    twitch_client_secret: str | None = Field(None, max_length=200)


class ExportIn(BaseModel):
    kind: Literal["video", "cover", "both"] = "video"


class PickIn(BaseModel):
    kind: Literal["video", "cover"] = "video"


def create_app(settings: Settings, runner=None, presence=None) -> FastAPI:
    app = FastAPI(title="Clipador GTA 6")

    if presence is not None:
        @app.middleware("http")
        async def _presence(request, call_next):
            if request.url.path.startswith("/api/") and request.url.path != "/api/bye":
                presence.seen()
            return await call_next(request)

    @app.post("/api/bye", status_code=204)
    def bye():
        """A aba do painel está fechando (sendBeacon). Se nenhuma aba voltar em segundos, o Clipador fecha."""
        if presence is not None:
            presence.bye()
        return Response(status_code=204)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.data_dir), name="media")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.mount("/fonts", StaticFiles(directory=FONTS), name="fonts")
    background: list[str] = []  # tarefas em andamento (VOD, teste, refazer card)

    def note(msg: str) -> None:
        if runner:
            runner.note(msg)
        else:
            log.info(msg)

    def streamers():
        return {s.name: s for s in load_config()[1]}

    def bg(label: str, fn):
        def wrap():
            background.append(label)
            try:
                fn()
            except Exception as e:
                log.exception(label)
                note(f"❌ {label}: {e}")
            finally:
                background.remove(label)
        threading.Thread(target=wrap, daemon=True).start()

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/webcam")
    def webcam_page():
        return FileResponse(STATIC / "webcam.html")

    @app.get("/api/config")
    def config():
        return {"handle": settings.handle, "accent": settings.accent, "max_clip_s": settings.max_clip_s}

    # ---------- configurações (salvas fora da pasta do Clipador) ----------
    def _hint(env: str) -> str:
        ks = [k for k in re.split(r"[\s,;]+", os.environ.get(env, "")) if k]
        if not ks:
            return ""
        k = ks[0]
        return f"{k[:4]}…{k[-4:]}" + (f" +{len(ks) - 1}" if len(ks) > 1 else "")

    def save_dirs():
        return resolve_dirs(settings.save_video_dir, settings.save_cover_dir)

    def settings_out() -> dict:
        k = groq_key() or ""
        return {"keys": {p.name: _hint(p.env) for p in PROVIDERS},
                "signup": {p.name: p.signup for p in PROVIDERS}, "handle": settings.handle, "min_score": settings.min_score, "card_seconds": settings.card_seconds,
                "require_gta": settings.require_gta, "zoom": settings.zoom, "auto_webcam": settings.auto_webcam,
                "auto_discover": settings.auto_discover, "discover_min_viewers": settings.discover_min_viewers,
                "save_video_dir": str(save_dirs()[0]), "save_cover_dir": str(save_dirs()[1]),
                "quality": ("max" if settings.quality == QUALITY_MAX else
                            "hd" if settings.quality.startswith("1080") else "leve"),
                "groq_key_set": bool(k),
                "groq_key_hint": f"{k[:4]}…{k[-4:]}" if len(k) > 12 else "", "saved_in": str(user_dir()),
                "twitch": {"set": twitchclips.has_credentials(), "id_hint": _hint("TWITCH_CLIENT_ID"),
                           "signup": twitchclips.SIGNUP}}

    @app.get("/api/settings")
    def get_settings():
        return settings_out()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        changes = body.model_dump(exclude_none=True)
        keys = {env: changes.pop(f, None) for f, env in
                (("groq_key", "GROQ_API_KEY"), ("cerebras_key", "CEREBRAS_API_KEY"), ("gemini_key", "GEMINI_API_KEY"),
                 ("twitch_client_id", "TWITCH_CLIENT_ID"), ("twitch_client_secret", "TWITCH_CLIENT_SECRET"))}
        if "quality" in changes:  # vale pras lives que começarem a gravar depois de salvar
            changes["quality"] = {"max": QUALITY_MAX, "hd": QUALITY_HD}.get(changes["quality"], QUALITY_LIGHT)
        for k in ("save_video_dir", "save_cover_dir"):
            if k in changes:
                v = changes[k].strip().strip("\"")
                if v and not Path(v).is_absolute():
                    raise HTTPException(400, "use o caminho completo da pasta (ex.: C:\\Users\\voce\\Videos)")
                changes[k] = v
        if "handle" in changes:
            h = changes["handle"].strip()
            changes["handle"] = h if not h or h.startswith("@") else "@" + h
        if changes:
            save_user_settings(changes)
            for k, v in changes.items():
                setattr(settings, k, v)
        for env, val in keys.items():
            if val is not None and val.strip():
                # várias chaves: uma por linha ou separadas por vírgula
                save_user_env(env, ",".join(k for k in re.split(r"[\s,;]+", val) if k))
        reset_ai_state()
        return settings_out()

    @app.post("/api/settings/test-twitch")
    def test_twitch():
        h = twitchclips.helix()
        if h is None:
            raise HTTPException(400, "Cole o Client ID e o Client Secret da Twitch")
        try:
            uid = h.user_id("twitch")
        except Exception as e:
            raise HTTPException(502, f"A Twitch recusou as chaves: {e}")
        return {"ok": bool(uid)}

    @app.post("/api/settings/test-groq")
    def test_groq():
        if not any_key("text"):
            raise HTTPException(400, "Cole pelo menos uma chave (Groq, Cerebras ou Gemini)")
        res = check_providers()
        if not any(v.startswith("ok") for v in res.values()):
            raise HTTPException(502, "Nenhuma chave funcionou: " + ", ".join(f"{k}: {v}" for k, v in res.items()))
        return {"ok": True, "providers": res}

    @app.get("/api/status")
    def status():
        st = runner.status() if runner else {"live": [], "queue": 0, "processing": [], "log": [], "watching": 0}
        st["background"] = list(background)
        st["running"] = runner is not None
        st["groq"] = any_key("text")
        st.setdefault("auto", bool(settings.auto_clip))
        st["vods"] = [{"id": t["id"], "label": t["title"] or t["streamer"], "stage": t["stage"]}
                     for t in vods.list() if t["state"] in ("rodando", "na fila")]
        return st

    # ---------- clipes ----------
    @app.get("/api/clips")
    def clips(posted: bool | None = None, streamer: str | None = None, trash: bool = False):
        out = []
        for c in db.list_clips(posted, streamer, trash=trash):
            j = c.to_json()
            try:  # versão = quando o vídeo foi gerado: muda ao refazer, então o navegador não mostra o antigo
                j["v"] = int((settings.data_dir / c.video_path).stat().st_mtime)
            except OSError:
                j["v"] = 0
            out.append(j)
        return out

    @app.post("/api/clips/{clip_id}/posted")
    def posted(clip_id: int, body: PostedIn):
        db.mark_posted(clip_id, body.posted)
        return {"ok": True}

    @app.patch("/api/clips/{clip_id}")
    def edit(clip_id: int, body: EditIn):
        db.update_clip(clip_id, **{k: v for k, v in body.model_dump().items() if v is not None})
        return db.get_clip(clip_id).to_json()

    @app.post("/api/clips/{clip_id}/card")
    def card(clip_id: int, body: CardIn):
        if not db.get_clip(clip_id):
            raise HTTPException(404)
        db.update_clip(clip_id, status="refazendo")
        def run():
            try:
                rerender_card(clip_id, body.lines, settings)
            finally:
                db.update_clip(clip_id, status="pronto")
        bg(f"refazendo card #{clip_id}", run)
        return {"ok": True}

    def _trash(clip_id: int) -> bool:
        """Excluir = mandar pra Lixeira. Nenhum arquivo é apagado aqui."""
        c = db.get_clip(clip_id)
        if not c:
            return False
        db.trash_clip(clip_id)
        return True

    def _purge(clip_id: int) -> bool:
        """Apagar de vez: só pra quem já está na Lixeira."""
        c = db.get_clip(clip_id)
        if not c or not c.deleted_at:
            return False
        folder = settings.data_dir / c.workdir
        try:  # se o Windows não deixar apagar (vídeo aberto), a marca impede que ele volte na recuperação
            folder.mkdir(parents=True, exist_ok=True)
            (folder / GONE_MARK).write_text("apagado pelo usuário", encoding="utf-8")
        except OSError:
            pass
        shutil.rmtree(folder, ignore_errors=True)
        db.delete_clip(clip_id)
        return True

    @app.post("/api/clips/{clip_id}/export")
    def export_one(clip_id: int, body: ExportIn):
        """Salva direto na pasta escolhida (sem janela "Salvar como"), já com .mp4/.jpg no nome."""
        c = db.get_clip(clip_id)
        if not c:
            raise HTTPException(404)
        vdir, cdir = save_dirs()
        try:
            return {"paths": export_clip(c, settings.data_dir, body.kind, vdir, cdir)}
        except (OSError, FileNotFoundError) as e:
            raise HTTPException(500, f"não deu pra salvar: {e}")

    @app.post("/api/clips/bulk-export")
    def export_many(body: IdsIn):
        vdir, cdir = save_dirs()
        paths = []
        for i in body.ids:
            c = db.get_clip(i)
            if c:
                try:
                    paths += export_clip(c, settings.data_dir, "both", vdir, cdir)
                except (OSError, FileNotFoundError):
                    log.exception("salvar clipe %s", i)
        return {"paths": paths, "video_dir": str(vdir), "cover_dir": str(cdir)}

    @app.post("/api/pick-folder")
    def pick_folder(body: PickIn):
        """Seletor de pasta do Windows (só na janela do app; no navegador, digite o caminho)."""
        from .. import desktop
        start = str(save_dirs()[0 if body.kind == "video" else 1])
        got = desktop.pick_folder(start)
        if not got:
            raise HTTPException(409, "não escolheu pasta (no navegador, digite o caminho no campo)")
        return {"path": got}

    @app.post("/api/clips/bulk-delete")
    def bulk_delete(body: IdsIn):
        return {"trashed": sum(_trash(i) for i in body.ids)}

    @app.post("/api/clips/bulk-restore")
    def bulk_restore(body: IdsIn):
        for i in body.ids:
            db.restore_clip(i)
        return {"ok": True}

    @app.post("/api/clips/{clip_id}/restore")
    def restore(clip_id: int):
        if not db.get_clip(clip_id):
            raise HTTPException(404)
        db.restore_clip(clip_id)
        return {"ok": True}

    @app.post("/api/clips/bulk-purge")
    def bulk_purge(body: IdsIn):
        return {"purged": sum(_purge(i) for i in body.ids)}

    @app.post("/api/trash/empty")
    def empty_trash():
        return {"purged": sum(_purge(c.id) for c in db.list_clips(trash=True))}

    @app.post("/api/clips/bulk-posted")
    def bulk_posted(body: IdsIn):
        for i in body.ids:
            db.mark_posted(i, body.posted)
        return {"ok": True}

    @app.post("/api/clips/zip")
    def bulk_zip(body: IdsIn):
        """Baixa vários clipes num .zip: vídeo, capa e texto do post de cada um."""
        import zipfile
        tmp = settings.data_dir / "zips"
        tmp.mkdir(parents=True, exist_ok=True)
        for old in tmp.glob("*.zip"):
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink(missing_ok=True)
        out = tmp / f"clipes_{time.strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            for i in body.ids:
                c = db.get_clip(i)
                if not c:
                    continue
                base = f"{c.display.replace(' ', '')}_{c.id}"
                video, cover = settings.data_dir / c.video_path, settings.data_dir / c.cover_path
                if video.exists():
                    z.write(video, f"{base}/{base}.mp4")
                if cover.exists():
                    z.write(cover, f"{base}/{base}_capa.jpg")
                post = f"{c.title}\n\n{c.description}\n\n{' '.join(c.hashtags)}\n"
                z.writestr(f"{base}/post.txt", post)
        return FileResponse(out, filename=out.name, media_type="application/zip")

    @app.delete("/api/clips/{clip_id}")
    def delete(clip_id: int):
        if not _trash(clip_id):
            raise HTTPException(404)
        return {"ok": True}

    # ---------- streamers / webcam ----------
    @app.get("/api/streamers")
    def list_streamers():
        live = {w["name"] for w in (runner.status()["live"] if runner else [])}
        extras = extra_streamer_names()
        return [{"name": s.name, "display": s.display, "lang": s.lang, "enabled": s.enabled,
                 "webcam": s.webcam, "live": s.name in live, "extra": s.name in extras,
                 "channels": [{"platform": c.platform, "channel": c.channel, "url": c.url} for c in s.channels]}
                for s in streamers().values()]

    @app.post("/api/streamers")
    def add_streamer(body: AddStreamerIn):
        """Põe um streamer/youtuber na lista pelo nome + link(s) do canal. Se estiver ao vivo, já começa a gravar."""
        from .. import discover
        chans, seen = [], set()
        for part in re.split(r"[\s,;]+", body.links):
            ch = discover.parse_channel_url(part)
            if ch and (ch.platform, ch.channel.lower()) not in seen:
                seen.add((ch.platform, ch.channel.lower()))
                chans.append(ch)
        if not chans:
            raise HTTPException(400, "não reconheci nenhum link de canal (Twitch, Kick ou YouTube)")
        name = discover._norm(body.display) or discover.auto_name(chans[0].platform, chans[0].channel)
        s = Streamer(name, body.display.strip(), body.lang, chans)
        save_extra_streamer(s)
        if runner:
            runner.reload()
            runner.wake()
        return {"ok": True, "name": name, "channels": [{"platform": c.platform, "channel": c.channel} for c in chans]}

    @app.delete("/api/streamers/{name}")
    def remove_streamer(name: str):
        if name not in extra_streamer_names():
            raise HTTPException(400, "só dá pra tirar quem você adicionou (os da lista padrão: desmarque em Ao vivo)")
        remove_extra_streamer(name)
        if runner:
            runner.forget(name)
        return {"ok": True}

    @app.post("/api/streamers/{name}/snapshot")
    def snap(name: str):
        s = streamers().get(name)
        if not s:
            raise HTTPException(404)
        out = settings.data_dir / "snapshots" / f"{name}.png"
        ok = False
        w = runner.workers.get(name) if runner else None
        if w and w.recorder.segments():
            ok = frame_from_file(w.recorder.segments()[-1].path, out)
        if not ok:
            for ch in s.channels:
                if probe(ch) and snapshot(ch.url, out, settings.quality):
                    ok = True
                    break
        if not ok:
            raise HTTPException(409, "Streamer offline — tente quando ele estiver ao vivo, "
                                     "ou envie um print (1280x720) da live.")
        return {"url": f"/media/snapshots/{name}.png?v={int(time.time())}"}

    @app.post("/api/streamers/{name}/snapshot-upload")
    async def snap_upload(name: str, file: UploadFile = File(...)):
        tmp = settings.data_dir / "snapshots" / f"{name}_upload"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(await file.read())
        out = settings.data_dir / "snapshots" / f"{name}.png"
        if not frame_from_file(tmp, out):
            raise HTTPException(400, "arquivo inválido")
        tmp.unlink(missing_ok=True)
        return {"url": f"/media/snapshots/{name}.png?v={int(time.time())}"}

    @app.put("/api/streamers/{name}/webcam")
    def set_webcam(name: str, body: WebcamIn):
        if name not in streamers():
            raise HTTPException(404)
        rect = None
        if body.rect:
            x, y, w, h = (int(v) for v in body.rect)
            x, y = max(0, min(x, 1270)), max(0, min(y, 710))
            w, h = max(10, min(w, 1280 - x)), max(10, min(h, 720 - y))
            rect = (x, y, w, h)
        save_webcam(settings, name, rect)
        if runner:
            runner.reload()
        return {"ok": True, "webcam": rect}

    # ---------- ligar/parar e escolher quem clipar ----------
    @app.post("/api/auto")
    def set_auto(body: AutoIn):
        save_user_settings({"auto_clip": body.on})
        if runner:
            runner.set_auto(body.on)
        else:
            settings.auto_clip = body.on
        return {"auto": settings.auto_clip}

    @app.put("/api/streamers/enabled")
    def set_enabled(body: EnabledIn):
        known = streamers()
        unknown = [n for n in body.enabled if n not in known]
        if unknown:
            raise HTTPException(404, f"streamer desconhecido: {', '.join(unknown)}")
        save_streamers_enabled(body.enabled)
        if runner:
            runner.set_enabled(body.enabled)
        return {"ok": True}

    @app.post("/api/streamers/{name}/clip")
    def clip_now(name: str):
        if not runner or not runner.force_clip(name):
            raise HTTPException(409, "esse streamer não está sendo gravado agora")
        return {"ok": True}

    # ---------- VOD e teste ----------
    from ..vodjobs import VodManager
    vods = VodManager(settings, streamers, note)
    app.state.vods = vods

    def identify(url: str) -> dict:
        from .. import discover
        f = discover.identify_vod(url)
        if f is None:
            return {"found": False}
        s = discover.match_streamer(f, list(streamers().values()))
        if s is not None:
            return {"found": True, "new": False, "name": s.name, "display": s.display, "platform": f.platform}
        novo = discover.to_streamer(f)
        return {"found": True, "new": True, "name": novo.name, "display": novo.display, "platform": f.platform,
                "_streamer": novo}

    @app.get("/api/vod/identify")
    def vod_identify(url: str):
        r = identify(url)
        r.pop("_streamer", None)
        return r

    @app.post("/api/vod")
    def vod(body: VodIn):
        if not body.url.strip().lower().startswith(("http://", "https://")):
            raise HTTPException(400, "cole o link completo da VOD (https://…)")
        name = body.streamer
        if not name:  # descobre de quem é pelo link (e põe na lista se for alguém novo)
            r = identify(body.url.strip())
            if not r["found"]:
                raise HTTPException(400, "não reconheci de quem é esse link — escolha o streamer na lista")
            if r["new"]:
                save_extra_streamer(r["_streamer"])
                if runner:
                    runner.reload()
            name = r["name"]
        if not streamers().get(name):
            raise HTTPException(404, "streamer desconhecido")
        return {"ok": True, "id": vods.create(body.url, name, body.top_n)}

    @app.get("/api/vods")
    def vod_list():
        return vods.list()

    @app.post("/api/vods/{tid}/pause")
    def vod_pause(tid: str):
        if not vods.pause(tid):
            raise HTTPException(409, "essa tarefa não está rodando")
        return {"ok": True}

    @app.post("/api/vods/{tid}/resume")
    def vod_resume(tid: str):
        if not vods.resume(tid):
            raise HTTPException(409, "essa tarefa não está pausada")
        return {"ok": True}

    @app.delete("/api/vods/{tid}")
    def vod_delete(tid: str):
        if not vods.delete(tid):
            raise HTTPException(404)
        return {"ok": True}

    @app.post("/api/test-clip")
    async def test_clip(streamer: str = Form(...), peak: float = Form(20.0), file: UploadFile = File(...)):
        s = streamers().get(streamer)
        if not s:
            raise HTTPException(404, "streamer desconhecido")
        from ..pipeline import new_workdir
        wd = new_workdir(settings, s)
        raw = wd / "upload.mp4"
        raw.write_bytes(await file.read())
        from ..cutter import cut_clip
        from ..recorder import Segment
        from ..render import probe_duration
        def run():
            dur = probe_duration(raw)
            b, a = settings.analysis_before_s, settings.analysis_after_s
            src, _, off = cut_clip([Segment(raw, 0.0, dur)], peak, b, a, b + a, wd / "analysis.mp4")
            clip = process_candidate(s, src, off, settings, platform="teste", live_title="teste", workdir=wd,
                                     manual=True, note=note)
            raw.unlink(missing_ok=True)
            note(f"✅ clipe de teste ({s.display}) pronto: {clip.title}")
        bg(f"teste {s.display}", run)
        return {"ok": True}

    return app
