"""Installed audio plugin inventory.

Discovery only. Loading a plugin is native work that belongs in a separate
process - see plugins.py for why that separation exists.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

from .. import db, plugin_host, plugins

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

_cache: dict | None = None


@router.get("")
async def list_plugins(refresh: bool = False):
    """Scanning walks several Program Files trees, so the result is cached
    until explicitly refreshed - plugin folders don't change mid-session."""
    global _cache
    if _cache is None or refresh:
        _cache = plugins.scan_all()
    return {**_cache, "host_available": plugin_host.host_available()}


@router.get("/describe")
async def describe_plugin(path: str):
    """Load the plugin and report what it really is - name, buses and
    parameters. This is the only way to get a parameter list: it lives in the
    plugin's code, not in any file next to it."""
    return await plugin_host.describe(path)


@router.post("/editor")
async def open_editor(body: dict = Body(...)):
    """Open the plugin's own interface.

    The window belongs to the host process, not to the browser, so this
    returns immediately with a job to poll; the job finishes when the user
    closes the window, at which point the plugin's settings have been saved
    under the slot's state key and a print will pick them up.
    """
    plugin_path = (body.get("plugin_path") or "").strip()
    if not plugin_path or not Path(plugin_path).exists():
        raise HTTPException(status_code=400, detail="plugin_path missing or not found")
    if not plugin_host.host_available():
        raise HTTPException(status_code=503, detail="the VST host is not built - see host/CMakeLists.txt")

    state_key = (body.get("state_key") or "").strip()
    if not state_key:
        raise HTTPException(status_code=400, detail="state_key missing - it names the insert slot")

    job_id = plugin_host.open_editor(plugin_path=plugin_path, state_key=state_key)
    return {"job_id": job_id, **plugin_host.editor_status(job_id)}


@router.get("/editor/{job_id}")
async def editor_status(job_id: str):
    return plugin_host.editor_status(job_id)


@router.post("/editor/{job_id}/close")
async def close_editor(job_id: str):
    return await plugin_host.close_editor(job_id)


@router.get("/state")
async def state_present(state_key: str):
    """Whether this slot has settings saved, so the UI can say whether a print
    would use the plugin's defaults or something the user actually set up."""
    return {"state_key": state_key, "saved": plugin_host.has_state(state_key)}


@router.post("/process")
async def process(body: dict = Body(...)):
    """Print a plugin onto a track, returning a job to poll.

    Offline by design: the DAW's playback runs in the browser, so a native
    plugin cannot sit in the live signal path - it renders a new take instead.
    """
    plugin_path = (body.get("plugin_path") or "").strip()
    if not plugin_path or not Path(plugin_path).exists():
        raise HTTPException(status_code=400, detail="plugin_path missing or not found")
    if not plugin_host.host_available():
        raise HTTPException(status_code=503, detail="the VST host is not built - see host/CMakeLists.txt")

    track_id: Optional[int] = body.get("track_id")
    stem: Optional[str] = body.get("stem")
    row = db.get_track(track_id) if track_id is not None else None
    if not row:
        raise HTTPException(status_code=404, detail="track not found")

    source = Path(row["audio_path"])
    if stem:
        stems = db.get_track_stems(track_id)
        if stem not in stems:
            raise HTTPException(status_code=400, detail=f"track has no '{stem}' stem")
        source = Path(stems[stem])

    params = {int(k): float(v) for k, v in (body.get("params") or {}).items()}
    job_id = plugin_host.start(
        source=source,
        plugin_path=plugin_path,
        params=params,
        title=body.get("title") or "",
        state_key=(body.get("state_key") or "").strip() or None,
    )
    return {"job_id": job_id, **plugin_host.job_status(job_id)}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    return plugin_host.job_status(job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return await plugin_host.cancel(job_id)
