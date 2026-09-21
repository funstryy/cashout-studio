"""The co-producer's endpoints.

Deliberately not on the sidebar and not a page of its own: this only makes
sense next to the thing it is listening to, so it lives in the DAW and
nowhere else.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from .. import coproducer, harmony
from ..native_engine import EngineUnavailable, engine

router = APIRouter(prefix="/api/coproducer", tags=["coproducer"])


@router.post("/listen")
async def listen(body: dict = Body(default={})):
    """What it can hear right now, and what it makes of it.

    Returns notes rather than a verdict. A mix has several things going on
    at once and ranking them into a single score would throw away the only
    useful part - which specific thing to go and fix.
    """
    project = body.get("project") if isinstance(body.get("project"), dict) else None
    seconds = float(body.get("seconds") or 8.0)

    try:
        heard: dict[str, Any] = engine.call(cmd="listen", seconds=seconds, tracks=32)
    except EngineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None

    if not heard.get("ok"):
        # Not an error worth a 500: "nothing is playing" is the normal state
        # of a DAW most of the time, and the panel says so rather than
        # showing a failure.
        return {
            "listening": False,
            "reason": heard.get("error", "nothing to listen to"),
            "notes": [note.as_dict() for note in coproducer.arrangement_notes(project or {})]
            if project else [],
        }

    if heard.get("silent"):
        return {
            "listening": True,
            "silent": True,
            "notes": [note.as_dict() for note in coproducer.arrangement_notes(project or {})]
            if project else [],
        }

    notes = coproducer.listen_notes(
        heard.get("analysis") or {},
        heard.get("tracks") or [],
        project,
    )
    # The musical half. Mix notes and harmony notes are the same kind of
    # thing to the panel, and both describe the same eight seconds.
    harmony_notes = harmony.judge(heard.get("harmony") or {})
    if project:
        notes.extend(coproducer.arrangement_notes(project))

    return {
        "listening": True,
        "silent": False,
        "position": heard.get("position", 0.0),
        "analysis": heard.get("analysis"),
        "harmony": heard.get("harmony"),
        "notes": [note.as_dict() for note in notes] + harmony_notes,
    }


@router.post("/review")
async def review(body: dict = Body(...)):
    """Arrangement notes with no audio needed, for when nothing is playing."""
    project = body.get("project")
    if not isinstance(project, dict):
        raise HTTPException(status_code=400, detail="send the project to look at")
    return {"notes": [note.as_dict() for note in coproducer.arrangement_notes(project)]}
