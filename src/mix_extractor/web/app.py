"""FastAPI application for the mix-extractor web GUI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mix_extractor.config import get_settings

_HERE = Path(__file__).resolve().parent

app = FastAPI(title="mix-extractor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))

# ── in-memory job state ────────────────────────────────────────────────────────
# Keyed by job_id (str).  Values: {"status": "pending|running|done|error", "log": [...], "mix_name": str}
_JOBS: dict[str, dict[str, Any]] = {}


# ── helpers ────────────────────────────────────────────────────────────────────

def _settings():
    return get_settings()


def _regenerate_search_links(track: dict) -> None:
    """Regenerate Bandcamp and SoundCloud search URLs using the track's current artist/title."""
    query = f"{track.get('artist', '')} {track.get('title', '')}"
    track.setdefault("links", {})
    track["links"]["bandcamp"] = f"https://bandcamp.com/search?q={quote_plus(query)}&item_type=t"
    track["links"]["soundcloud"] = f"https://soundcloud.com/search/tracks?q={quote_plus(query)}"


def _apply_user_data_to_track(track: dict, td: dict) -> None:
    """Mutate *track* in-place, merging user_data entry *td*.

    Applies keep flag, genre, field overrides (artist/title/label/remix),
    regenerates search links when artist or title changed, then applies
    manual link overrides on top (highest priority).
    """
    track["keep"] = td.get("keep", False)
    track["genre"] = td.get("genre", track.get("genre", ""))

    has_search_field_override = False
    for field, user_val in td.get("overrides", {}).items():
        if field in ("artist", "title", "label", "remix") and user_val:
            track[f"_original_{field}"] = track.get(field, "")
            track[field] = user_val
            if field in ("artist", "title"):
                has_search_field_override = True

    # Regenerate search-based links when artist/title were overridden
    if has_search_field_override:
        _regenerate_search_links(track)

    # Apply manual link overrides last — these always win
    for service, url in td.get("link_overrides", {}).items():
        if url:
            track.setdefault("links", {})
            track["links"][service] = url
            track[f"_link_override_{service}"] = True


def _load_all_mixes() -> list[dict]:
    """Return a list of mix metadata dicts loaded from all tracks.json files."""
    settings = _settings()
    mixes = []
    if not settings.output_dir.exists():
        return mixes
    for mix_dir in sorted(settings.output_dir.iterdir()):
        tracks_file = mix_dir / "tracks.json"
        if not tracks_file.exists():
            continue
        try:
            data = json.loads(tracks_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        user_data = _load_user_data(mix_dir.name)
        mix_info = data.get("mix", {})
        tracks = data.get("tracks", [])
        # Merge user data (keep flags, genres, field overrides) into tracks
        for track in tracks:
            tid = _track_id(mix_dir.name, track)
            td = user_data.get("tracks", {}).get(tid, {})
            _apply_user_data_to_track(track, td)
        mixes.append(
            {
                "name": mix_dir.name,
                "source": mix_info.get("source", mix_dir.name),
                "analyzed_at": mix_info.get("analyzed_at", ""),
                "duration_seconds": mix_info.get("duration_seconds"),
                "transcription_provider": mix_info.get("transcription_provider", ""),
                "track_count": len(tracks),
                "tracks": tracks,
            }
        )
    return mixes


def _load_mix(mix_name: str) -> dict | None:
    settings = _settings()
    tracks_file = settings.output_dir / mix_name / "tracks.json"
    if not tracks_file.exists():
        return None
    try:
        data = json.loads(tracks_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    user_data = _load_user_data(mix_name)
    for track in data.get("tracks", []):
        tid = _track_id(mix_name, track)
        td = user_data.get("tracks", {}).get(tid, {})
        _apply_user_data_to_track(track, td)
    return data


def _user_data_path(mix_name: str) -> Path:
    settings = _settings()
    return settings.output_dir / mix_name / "user_data.json"


def _load_user_data(mix_name: str) -> dict:
    path = _user_data_path(mix_name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_user_data(mix_name: str, data: dict) -> None:
    path = _user_data_path(mix_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _track_id(mix_name: str, track: dict) -> str:
    """Stable identifier for a track within a mix (index-based)."""
    return str(track.get('index', 0))


def _list_input_files() -> list[dict]:
    settings = _settings()
    audio_exts = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".webm", ".mp4"}
    if not settings.input_dir.exists():
        return []
    files = []
    for p in sorted(settings.input_dir.iterdir()):
        if p.suffix.lower() in audio_exts:
            files.append({"name": p.name, "size_mb": round(p.stat().st_size / (1024 * 1024), 1)})
    return files


def _run_analyze_job(job_id: str, source: str, options: dict) -> None:
    """Background task: run mix-extractor analyze in a subprocess."""
    _JOBS[job_id]["status"] = "running"
    cmd = [sys.executable, "-m", "mix_extractor.cli", "analyze", source]
    if options.get("no_enrich"):
        cmd.append("--no-enrich")
    if options.get("no_fingerprint"):
        cmd.append("--no-fingerprint")
    if options.get("fingerprint_only"):
        cmd.append("--fingerprint-only")
    if options.get("llm"):
        cmd += ["--llm", options["llm"]]
    if options.get("model"):
        cmd += ["--model", options["model"]]
    if options.get("transcriber"):
        cmd += ["--transcriber", options["transcriber"]]
    try:
        # Stream output line by line so the user sees progress
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            universal_newlines=True,
        )

        _JOBS[job_id]["log"] = []
        if process.stdout:
            for line in process.stdout:
                line = line.rstrip()
                print(line)  # Also print to server console
                _JOBS[job_id]["log"].append(line)

        returncode = process.wait()
        _JOBS[job_id]["status"] = "done" if returncode == 0 else "error"

    except Exception as exc:
        _JOBS[job_id]["status"] = "error"
        _JOBS[job_id]["log"].append(f"Error: {exc}")


# ── pages ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    mixes = _load_all_mixes()
    input_files = _list_input_files()
    jobs = {jid: j for jid, j in _JOBS.items() if j["status"] in ("pending", "running")}
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mixes": mixes,
            "input_files": input_files,
            "active_jobs": jobs,
        },
    )


@app.get("/library", response_class=HTMLResponse)
async def library(request: Request, q: str = "", keep_only: bool = False):
    mixes = _load_all_mixes()
    all_tracks = []
    for mix in mixes:
        for track in mix["tracks"]:
            all_tracks.append(
                {
                    **track,
                    "mix_name": mix["name"],
                    "mix_source": mix["source"],
                }
            )
    if q:
        ql = q.lower()
        all_tracks = [
            t
            for t in all_tracks
            if ql in t.get("artist", "").lower()
            or ql in t.get("title", "").lower()
            or ql in t.get("label", "").lower()
            or ql in t.get("genre", "").lower()
        ]
    if keep_only:
        all_tracks = [t for t in all_tracks if t.get("keep")]
    return templates.TemplateResponse(
        "library.html",
        {
            "request": request,
            "tracks": all_tracks,
            "q": q,
            "keep_only": keep_only,
            "total": len(all_tracks),
        },
    )


@app.get("/mix/{mix_name}", response_class=HTMLResponse)
async def mix_detail(request: Request, mix_name: str):
    data = _load_mix(mix_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Mix not found")
    settings = _settings()
    user_data = _load_user_data(mix_name)
    return templates.TemplateResponse(
        "mix.html",
        {
            "request": request,
            "mix_name": mix_name,
            "mix": data.get("mix", {}),
            "tracks": data.get("tracks", []),
            "buymusic_club_configured": bool(
                settings.buymusic_club_username and settings.buymusic_club_password
            ),
            "buymusic_club_url": user_data.get("buymusic_club_url", ""),
        },
    )


# ── upload ─────────────────────────────────────────────────────────────────────

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    settings = _settings()
    settings.input_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.input_dir / (file.filename or "upload")
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return RedirectResponse(url="/", status_code=303)


# ── analyze ────────────────────────────────────────────────────────────────────

@app.post("/analyze")
async def start_analyze(
    background_tasks: BackgroundTasks,
    source: str = Form(...),
    no_enrich: bool = Form(False),
    no_fingerprint: bool = Form(False),
    fingerprint_only: bool = Form(False),
    llm: str = Form(""),
    model: str = Form(""),
    transcriber: str = Form(""),
):
    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {"status": "pending", "log": [], "source": source}
    options = {
        "no_enrich": no_enrich,
        "no_fingerprint": no_fingerprint,
        "fingerprint_only": fingerprint_only,
        "llm": llm or None,
        "model": model or None,
        "transcriber": transcriber or None,
    }
    background_tasks.add_task(_run_analyze_job, job_id, source, options)
    return RedirectResponse(url=f"/job/{job_id}", status_code=303)


# ── job status ─────────────────────────────────────────────────────────────────

@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_status(request: Request, job_id: str):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(
        "job.html",
        {"request": request, "job_id": job_id, "job": job},
    )


@app.get("/api/job/{job_id}")
async def job_status_api(job_id: str):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job)


# ── track management API ───────────────────────────────────────────────────────

@app.post("/api/track/{mix_name}/keep")
async def toggle_keep(mix_name: str, request: Request):
    body = await request.json()
    track_index = body.get("index")
    keep = body.get("keep", True)
    data = _load_mix(mix_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Mix not found")
    # Find the track
    track = next((t for t in data.get("tracks", []) if t.get("index") == track_index), None)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    tid = _track_id(mix_name, track)
    user_data = _load_user_data(mix_name)
    user_data.setdefault("tracks", {})
    user_data["tracks"].setdefault(tid, {})
    user_data["tracks"][tid]["keep"] = keep
    _save_user_data(mix_name, user_data)
    return JSONResponse({"ok": True, "keep": keep})


@app.post("/api/track/{mix_name}/genre")
async def set_genre(mix_name: str, request: Request):
    body = await request.json()
    track_index = body.get("index")
    genre = body.get("genre", "")
    data = _load_mix(mix_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Mix not found")
    track = next((t for t in data.get("tracks", []) if t.get("index") == track_index), None)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    tid = _track_id(mix_name, track)
    user_data = _load_user_data(mix_name)
    user_data.setdefault("tracks", {})
    user_data["tracks"].setdefault(tid, {})
    user_data["tracks"][tid]["genre"] = genre
    _save_user_data(mix_name, user_data)
    return JSONResponse({"ok": True, "genre": genre})


@app.post("/api/track/{mix_name}/edit")
async def edit_track_field(mix_name: str, request: Request):
    """Save a user override for an editable field. Original scraped data stays in tracks.json."""
    body = await request.json()
    track_index = body.get("index")
    field = body.get("field")
    value = body.get("value", "")
    _EDITABLE_FIELDS = {"artist", "title", "label", "remix"}
    if field not in _EDITABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"Field '{field}' is not editable")
    data = _load_mix(mix_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Mix not found")
    track = next((t for t in data.get("tracks", []) if t.get("index") == track_index), None)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    # _original_<field> is set by _load_mix when an override exists; otherwise field itself is scraped
    original = track.get(f"_original_{field}", track.get(field, ""))
    tid = _track_id(mix_name, track)
    user_data = _load_user_data(mix_name)
    user_data.setdefault("tracks", {})
    user_data["tracks"].setdefault(tid, {})
    user_data["tracks"][tid].setdefault("overrides", {})
    if value:
        user_data["tracks"][tid]["overrides"][field] = value
    else:
        user_data["tracks"][tid]["overrides"].pop(field, None)
    _save_user_data(mix_name, user_data)
    return JSONResponse({"ok": True, "field": field, "value": value, "original": original})


@app.post("/api/track/{mix_name}/link")
async def set_link_override(mix_name: str, request: Request):
    """Save (or clear) a manual URL override for a specific link service."""
    body = await request.json()
    track_index = body.get("index")
    service = body.get("service")
    url = (body.get("url") or "").strip()

    _LINK_SERVICES = {"bandcamp", "soundcloud", "spotify", "youtube_music", "musicbrainz", "discogs"}
    if service not in _LINK_SERVICES:
        raise HTTPException(status_code=400, detail=f"Service '{service}' is not supported")
    if url and not (url.startswith("https://") or url.startswith("http://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    data = _load_mix(mix_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Mix not found")
    track = next((t for t in data.get("tracks", []) if t.get("index") == track_index), None)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")

    tid = _track_id(mix_name, track)
    user_data = _load_user_data(mix_name)
    user_data.setdefault("tracks", {})
    user_data["tracks"].setdefault(tid, {})
    user_data["tracks"][tid].setdefault("link_overrides", {})
    if url:
        user_data["tracks"][tid]["link_overrides"][service] = url
    else:
        user_data["tracks"][tid]["link_overrides"].pop(service, None)
    _save_user_data(mix_name, user_data)
    return JSONResponse({"ok": True, "service": service, "url": url})


@app.get("/api/mixes")
async def api_mixes():
    """JSON list of all mixes for programmatic use."""
    mixes = _load_all_mixes()
    return JSONResponse(mixes)


@app.get("/api/library")
async def api_library(q: str = "", keep_only: bool = False):
    """JSON list of all tracks across all mixes."""
    mixes = _load_all_mixes()
    all_tracks = []
    for mix in mixes:
        for track in mix["tracks"]:
            all_tracks.append({**track, "mix_name": mix["name"]})
    if q:
        ql = q.lower()
        all_tracks = [
            t
            for t in all_tracks
            if ql in t.get("artist", "").lower()
            or ql in t.get("title", "").lower()
            or ql in t.get("label", "").lower()
            or ql in t.get("genre", "").lower()
        ]
    if keep_only:
        all_tracks = [t for t in all_tracks if t.get("keep")]
    return JSONResponse(all_tracks)


# ── buymusic.club publish ──────────────────────────────────────────────────────

@app.post("/api/mix/{mix_name}/publish")
async def publish_to_buymusic_club(mix_name: str, request: Request):
    """Publish a mix tracklist to buymusic.club and return the list URL."""
    body = await request.json()
    list_title = (body.get("title") or "").strip() or mix_name
    include_all = bool(body.get("include_all", False))

    settings = _settings()
    if not settings.buymusic_club_username or not settings.buymusic_club_password:
        raise HTTPException(
            status_code=400,
            detail="BUYMUSIC_CLUB_USERNAME and BUYMUSIC_CLUB_PASSWORD are not configured.",
        )

    data = _load_mix(mix_name)
    if data is None:
        raise HTTPException(status_code=404, detail="Mix not found")

    try:
        from mix_extractor.buymusic_club import publish_mix, BuymusicClubError  # noqa: PLC0415
        url = publish_mix(
            mix_name=mix_name,
            settings=settings,
            list_title=list_title,
            include_all=include_all,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"ok": True, "url": url})
