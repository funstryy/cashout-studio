"""The file browser API.

Every path that arrives here is re-checked against the allowed roots, not
just the ones the browser itself produced. A confinement check that only
runs while building the tree is not a confinement check - see browser.py.
"""
from __future__ import annotations

import mimetypes
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

from .. import browser

router = APIRouter(prefix="/api/browser", tags=["browser"])


def _checked(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not browser.within_roots(path):
        raise HTTPException(status_code=403, detail="that path is outside the browser's folders")
    return path


@router.get("/roots")
async def get_roots():
    return {"roots": [root.as_dict() for root in browser.roots()]}


@router.post("/roots")
async def post_root(path: str = Body(..., embed=True)):
    root = browser.add_root(path)
    if not root:
        raise HTTPException(status_code=400, detail="not a folder, or already added")
    return {"roots": [r.as_dict() for r in browser.roots()]}


@router.delete("/roots")
async def delete_root(path: str = Query(...)):
    if not browser.remove_root(path):
        raise HTTPException(status_code=404, detail="not one of the added folders")
    return {"roots": [r.as_dict() for r in browser.roots()]}


@router.get("/list")
async def list_dir(path: str = Query(...)):
    target = _checked(path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="no such folder")
    return {"path": str(target), "entries": [e.as_dict() for e in browser.listing(target)]}


@router.get("/search")
async def search(q: str = Query(...)):
    return {"query": q, "entries": [e.as_dict() for e in browser.search(q)]}


@router.get("/file")
async def get_file(path: str = Query(...)):
    """Streams a browsed file so it can be auditioned before importing."""
    target = _checked(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    media_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=media_type or "application/octet-stream")

@router.post("/import")
async def import_file(path: str = Body(..., embed=True)):
    """Copies a browsed file into the library as a track.

    The reason this exists rather than just using the upload endpoint: the
    studio runs inside a WebView, and `<input type="file">` there depends on
    the host implementing a native picker. When it does not, clicking the
    import box does nothing at all and there is no error to show - which is
    exactly what it looked like from the outside.

    Reading the file server-side sidesteps the question entirely. The path
    still has to be inside a configured root, so this opens nothing that the
    file browser was not already allowed to read.
    """
    from .. import db
    from ..audio_io import AudioConversionError

    target = _checked(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")

    extension = target.suffix.lower().lstrip(".")
    allowed = {"wav", "mp3", "flac", "ogg", "m4a", "aac", "wma", "aiff", "aif"}
    if extension not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{target.suffix or 'that'} is not an audio file the studio reads",
        )

    destination = db.model_dir("upload") / f"{int(time.time())}_{target.name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, destination)

    try:
        track_id = db.insert_track(
            model="upload",
            title=target.stem[:80],
            lyrics="",
            seed=None,
            duration_ms=None,
            wall_ms=None,
            params={"source": "file_browser", "from": str(target)},
            audio_path=destination,
            abc_path=None,
        )
    except (OSError, AudioConversionError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return {
        "ok": True,
        "id": track_id,
        "title": target.stem[:80],
        "audio_url": f"/api/tracks/{track_id}/audio",
    }
