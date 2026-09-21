"""The native audio engine, exposed to the UI.

Thin on purpose. Every endpoint here is a command on the engine's wire
protocol plus whatever the studio needs to do with the result - which, for
the capture, means landing a WAV in the library so it behaves like any other
track the moment it exists.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..native_engine import EngineUnavailable, capture_dir, engine
from .routes_users import current_user_id

router = APIRouter(prefix="/api/engine", tags=["engine"])


def _call(**command: Any) -> dict:
    try:
        result = engine.call(**command)
    except EngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    if not result.get("ok", False):
        raise HTTPException(status_code=400, detail=result.get("error", "the engine refused that"))
    return result


@router.get("/status")
async def status():
    return engine.status()


@router.post("/start")
async def start():
    if not engine.start():
        raise HTTPException(status_code=503, detail=engine.last_error)
    return engine.status()


@router.post("/stop")
async def stop():
    engine.stop()
    return {"ok": True, "running": False}


@router.get("/devices")
async def devices():
    return _call(cmd="devices")


@router.post("/open")
async def open_device(body: dict = Body(default={})):
    """Opens an output.

    Exclusive mode is never the default and never implicit: it takes the
    device away from everything else on the machine, so it has to be the
    user's deliberate choice rather than something the studio does because
    it wants the latency.
    """
    return _call(
        cmd="open",
        device=(body.get("device") or ""),
        sampleRate=int(body.get("sampleRate") or 48000),
        bufferFrames=int(body.get("bufferFrames") or 256),
        exclusive=bool(body.get("exclusive")),
    )


@router.post("/close")
async def close_device():
    return _call(cmd="close")


@router.get("/meters")
async def meters(count: int = 16):
    return _call(cmd="meters", count=count)


@router.post("/transport")
async def transport(body: dict = Body(default={})):
    action = (body.get("action") or "").strip()
    if action in ("play", "stop"):
        return _call(cmd=action)
    if action == "seek":
        return _call(cmd="seek", seconds=float(body.get("seconds") or 0.0))
    raise HTTPException(status_code=400, detail="action must be play, stop or seek")


@router.post("/insert")
async def insert(body: dict = Body(...)):
    """Puts a VST3 in a track's live signal path, or clears the slot.

    Not the same thing as the studio's existing plugin feature, which prints
    the effect onto the clip through a separate offline host. This one is in
    the path: turn a knob and you hear it, on the same buffer the mixer is
    about to sum.
    """
    return _call(
        cmd="insert",
        track=int(body.get("track") or 0),
        slot=int(body.get("slot") or 0),
        path=(body.get("path") or ""),
    )


@router.post("/capture")
async def capture(body: dict = Body(default={}), user_id: int = Depends(current_user_id)):
    """Writes down what you already played.

    The engine has been holding the last thirty seconds of the master bus
    since the device opened, because a ring buffer costs one memcpy a block
    and nothing to leave running. This is the button for "that was the take
    and I wasn't recording" - the audio already exists, it has just never
    been saved. It lands in the library as an ordinary track, so the DAW,
    the separator and the dataset builder can all see it immediately.
    """
    seconds = float(body.get("seconds") or 30.0)
    seconds = max(1.0, min(seconds, 30.0))

    destination = capture_dir() / f"capture_{int(time.time())}.wav"
    result = _call(cmd="capture", path=str(destination), seconds=seconds)

    if not destination.is_file():
        raise HTTPException(status_code=500, detail="the engine reported success but wrote nothing")

    title = (body.get("title") or "").strip() or f"Capture · last {int(result['seconds'])}s"
    try:
        track_id = db.insert_track(
            model="capture",
            title=title,
            lyrics="",
            seed=None,
            duration_ms=int(result["seconds"] * 1000),
            wall_ms=None,
            params={"source": "retrospective-capture", "seconds": result["seconds"]},
            audio_path=destination,
            abc_path=None,
        )
    except Exception as exc:  # noqa: BLE001 - the file exists; report why it did not land
        raise HTTPException(status_code=500, detail=f"captured, but could not save it: {exc}") from None

    return {
        "ok": True,
        "track_id": track_id,
        "seconds": result["seconds"],
        "title": title,
        "audio_url": f"/api/tracks/{track_id}/audio",
    }


# --------------------------------------------------------------- timeline

# The engine reads files, the timeline holds URLs. This is the only place
# that knows how to get from one to the other, and it is deliberately the
# backend's job: the engine has no business knowing what an /api/tracks URL
# is, and the browser has no business knowing where the library lives on
# disk.
_TRACK_AUDIO = re.compile(r"^/api/tracks/(\d+)/audio$")
_TRACK_STEM = re.compile(r"^/api/tracks/(\d+)/stems/([^/?]+)$")


def _resolve_source(url: str) -> Optional[Path]:
    """A clip's sourceUrl as a file the engine can open."""
    # A guest in a live session carries absolute URLs pointing at the host.
    # Those are somebody else's disk, so they resolve to nothing here - the
    # native path is local playback only, and saying so beats loading the
    # wrong track that happens to share an id.
    if url.startswith("http://") or url.startswith("https://"):
        return None
    clean = url.split("?")[0]

    match = _TRACK_AUDIO.match(clean)
    if match:
        row = db.get_track(int(match.group(1)))
        return db.resolve_media_path(row["audio_path"]) if row else None

    match = _TRACK_STEM.match(clean)
    if match:
        # stems_dir is keyed by model as well as id, so the track row has to
        # be read even though the id is right there in the URL.
        row = db.get_track(int(match.group(1)))
        if row is None:
            return None
        directory = db.stems_dir(row["model"], int(match.group(1)))
        for suffix in (".wav", ".flac", ".mp3"):
            candidate = directory / f"{match.group(2)}{suffix}"
            if candidate.is_file():
                return candidate
    return None


@router.post("/sync")
async def sync(body: dict = Body(...)):
    """Loads a whole arrangement into the engine.

    Sent wholesale rather than diffed. An arrangement is a few dozen clips
    and the engine reloads them in well under a second from the OS cache;
    tracking which clip moved would be a second copy of the timeline's state
    living somewhere it can drift out of date, to save time nobody notices.

    WAV only, because that is what the engine reads. Everything the studio
    generates lands as WAV, so in practice this only skips files a user
    dragged in from elsewhere - and it reports them rather than pretending
    they played.
    """
    project = body.get("project")
    if not isinstance(project, dict):
        raise HTTPException(status_code=400, detail="send the project to play")

    lanes = project.get("lanes") or []
    _call(cmd="clearClips")

    loaded: dict[str, str] = {}
    skipped: list[str] = []
    clips = 0

    for index, lane in enumerate(lanes):
        if index >= 128:
            break
        settings = lane.get("settings") or {}
        _call(
            cmd="track",
            index=index,
            gain=float(settings.get("volume", 1.0)),
            pan=float(settings.get("pan", 0.0)),
            mute=bool(settings.get("muted")),
            solo=bool(settings.get("solo") or settings.get("soloed")),
        )

        for clip in lane.get("clips") or []:
            url = str(clip.get("sourceUrl") or "")
            path = _resolve_source(url)
            if path is None or path.suffix.lower() != ".wav":
                skipped.append(clip.get("sourceLabel") or url)
                continue

            key = str(path)
            if key not in loaded:
                buffer_id = f"b{len(loaded)}"
                try:
                    _call(cmd="load", id=buffer_id, path=key)
                except HTTPException:
                    skipped.append(clip.get("sourceLabel") or url)
                    continue
                loaded[key] = buffer_id

            trim_start = float(clip.get("trimStart") or 0.0)
            trim_end = float(clip.get("trimEnd") or 0.0)
            if trim_end <= trim_start:
                continue
            _call(
                cmd="addClip",
                track=index,
                buffer=loaded[key],
                start=float(clip.get("timelineStart") or 0.0),
                trimStart=trim_start,
                trimEnd=trim_end,
                gain=1.0,
            )
            clips += 1

    master = project.get("master") or {}
    _call(cmd="master", gain=float(master.get("volume", 1.0)))

    loop = project.get("loop") or {}
    _call(
        cmd="loop",
        enabled=bool(loop.get("enabled")),
        start=float(loop.get("startSec") or 0.0),
        end=float(loop.get("endSec") or 0.0),
    )

    return {"ok": True, "clips": clips, "buffers": len(loaded), "skipped": skipped}


# -------------------------------------------------------------- mastering

def _mastered_dir() -> Path:
    directory = capture_dir().parent / "mastered"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _track_wav(track_id: int) -> Path:
    """A track as a WAV the engine can read, or a 400 explaining why not."""
    row = db.get_track(track_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no such track")
    path = db.resolve_media_path(row["audio_path"])
    if path is None:
        raise HTTPException(status_code=404, detail="that track's audio is missing")
    if path.suffix.lower() != ".wav":
        raise HTTPException(
            status_code=400,
            detail="the mastering engine reads WAV; this track is " + path.suffix,
        )
    return path


@router.get("/mastering/targets")
async def mastering_targets():
    return _call(cmd="targets")


@router.post("/mastering/analyze")
async def mastering_analyze(body: dict = Body(...)):
    """Measures a track without changing it.

    Worth having on its own: knowing a mix is at -9 LUFS with a true peak
    of +0.4 dBTP tells you it will be turned down by every streaming
    service and clipped by some of them, which is a thing to fix in the mix
    rather than paper over in the master.
    """
    return _call(cmd="analyze", path=str(_track_wav(int(body.get("track_id") or 0))))


@router.post("/mastering/master")
async def mastering_master(body: dict = Body(...), user_id: int = Depends(current_user_id)):
    """Masters a track and puts the result in the library beside the original.

    Beside, never over. A master is a new version, and the mix it came from
    is the thing you go back to when the master turns out wrong.
    """
    track_id = int(body.get("track_id") or 0)
    source = _track_wav(track_id)
    row = db.get_track(track_id)

    command: dict[str, Any] = {
        "cmd": "masterFile",
        "path": str(source),
        "out": str(_mastered_dir() / f"master_{track_id}_{int(time.time())}.wav"),
        "target": (body.get("target") or "streaming"),
    }
    # A reference track beats any preset, so it wins when both are given.
    reference_id = body.get("reference_track_id")
    if reference_id:
        command["reference"] = str(_track_wav(int(reference_id)))
    if body.get("lufs") is not None:
        command["lufs"] = float(body["lufs"])
    if body.get("ceiling") is not None:
        command["ceiling"] = float(body["ceiling"])

    result = _call(**command)
    out = Path(result["out"])
    if not out.is_file():
        raise HTTPException(status_code=500, detail="the engine reported success but wrote nothing")

    base = (row["title"] if row else "Track") or "Track"
    label = "reference" if reference_id else result.get("target", "master")
    title = f"{base} · {label} master"[:80]
    try:
        new_id = db.insert_track(
            model="master",
            title=title,
            lyrics=row["lyrics"] if row else "",
            seed=None,
            duration_ms=int(result["after"]["seconds"] * 1000),
            wall_ms=None,
            params={
                "source": "mastering",
                "from_track": track_id,
                "target": result.get("target"),
                "lufs": result["after"]["lufs"],
                "truePeakDb": result["after"]["truePeakDb"],
                "plan": result["plan"],
            },
            audio_path=out,
            abc_path=None,
        )
    except Exception as exc:  # noqa: BLE001 - the render exists; say why it did not land
        raise HTTPException(status_code=500, detail=f"mastered, but could not save it: {exc}") from None

    result["track_id"] = new_id
    result["title"] = title
    result["audio_url"] = f"/api/tracks/{new_id}/audio"
    return result
