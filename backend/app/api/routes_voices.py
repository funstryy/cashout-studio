"""Voice library endpoints: import a voice, speak with it, convert audio to it.

Speaking is proxied to audiocpp_server's OpenAI-style /v1/audio/speech, with
the Chatterbox model loaded on demand first - the frontend shouldn't have to
know that "use this voice" means "make sure a particular GGUF is resident".
Conversion is handed to voices.py, which drives audiocpp_cli as a job.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .. import audiocpp, db, voices
from ..audio_io import AudioConversionError, to_wav
from ..config import MODELS, voice_specs
from ..orchestrator.manager import manager
from ..orchestrator.state import ModelStatus
from .routes_proxy import _client

router = APIRouter(prefix="/api/voices", tags=["voices"])

YUE2_BASE = MODELS["yue2"].proxy_target


def _require_engine() -> None:
    """Both voice models live in audiocpp_server, which the orchestrator runs
    as the "yue2" model - so voices are only available while that engine is
    the active one."""
    if manager.state.models["yue2"].status != ModelStatus.RUNNING:
        raise HTTPException(
            status_code=503,
            detail="The YuE2 engine has to be running for voices - switch to it first.",
        )


async def _ensure_chatterbox_loaded() -> None:
    spec = voice_specs()["chatterbox"]
    try:
        resp = await audiocpp.ensure_loaded(_client, spec)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"could not reach the engine: {exc}") from exc
    if resp is not None and resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=(
                f"loading the Chatterbox voice model failed ({resp.status_code}): {resp.text[:400]}. "
                "Run setup_models.ps1 to download its weights if you haven't."
            ),
        )


@router.get("")
async def get_voices():
    return {"voices": voices.list_voices()}


@router.post("")
async def import_voice(
    name: str = Form(...),
    transcript: str = Form(""),
    audio: Optional[UploadFile] = File(None),
    track_id: Optional[int] = Form(None),
    stem: Optional[str] = Form(None),
):
    """A reference clip comes either from a file or from a track already in
    the library - usually that track's isolated vocal, which is the cleanest
    reference you can hand these models without leaving the app."""
    if audio is not None:
        data = await audio.read()
    elif track_id is not None:
        data = (await _resolve_source(track_id=track_id, stem=stem, audio=None)).read_bytes()
    else:
        raise HTTPException(status_code=400, detail="pass either an audio file or track_id")
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        return await voices.save_voice(name=name, data=data, transcript=transcript)
    except AudioConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{name}/audio")
async def get_voice_audio(name: str):
    path = voices.voice_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="voice not found")
    return FileResponse(path, media_type="audio/wav")


@router.delete("/{name}")
async def remove_voice(name: str):
    if not voices.delete_voice(name):
        raise HTTPException(status_code=404, detail="voice not found")
    return {"deleted": True}


@router.post("/{name}/speak")
async def speak(name: str, body: dict = Body(...)):
    if not voices.voice_path(name):
        raise HTTPException(status_code=404, detail="voice not found")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    _require_engine()
    await _ensure_chatterbox_loaded()

    payload = {
        "model": voice_specs()["chatterbox"]["id"],
        "input": text,
        "voice": voices.sanitize_name(name),
        "language": body.get("language") or "en",
    }
    for key in ("temperature", "guidance_scale", "seed"):
        if body.get(key) is not None:
            payload[key] = body[key]

    try:
        upstream = await _client.post(f"{YUE2_BASE}/v1/audio/speech", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"speech request failed: {exc}") from exc
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail=upstream.text[:600])
    return Response(content=upstream.content, media_type=upstream.headers.get("content-type", "audio/wav"))


@router.post("/{name}/convert")
async def convert(
    name: str,
    task: str = Form("svc"),
    title: str = Form(""),
    track_id: Optional[int] = Form(None),
    stem: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
):
    if not voices.voice_path(name):
        raise HTTPException(status_code=404, detail="voice not found")
    if task not in ("vc", "svc"):
        raise HTTPException(status_code=400, detail="task must be 'vc' (speech) or 'svc' (singing)")
    _require_engine()

    source = await _resolve_source(track_id=track_id, stem=stem, audio=audio)
    await audiocpp.release_vram(_client)
    job_id = voices.start_conversion(source=source, voice=name, task=task, title=title)
    return {"job_id": job_id, **voices.job_status(job_id)}


async def _resolve_source(*, track_id: Optional[int], stem: Optional[str], audio: Optional[UploadFile]) -> Path:
    """Convert either a saved track (optionally just one of its stems, which
    is what you want for singing - the isolated vocal, not the full mix) or a
    freshly uploaded file."""
    if track_id is not None:
        row = db.get_track(track_id)
        if not row:
            raise HTTPException(status_code=404, detail="track not found")
        if stem:
            stems = db.get_track_stems(track_id)
            if stem not in stems:
                raise HTTPException(
                    status_code=400,
                    detail=f"track {track_id} has no '{stem}' stem - separate it first.",
                )
            return Path(stems[stem])
        return Path(row["audio_path"])

    if audio is None:
        raise HTTPException(status_code=400, detail="pass either track_id or an audio file")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    try:
        wav = await to_wav(data, sample_rate=44100, channels=1)
    except AudioConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dest = db.model_dir("voices") / f"source_{voices.sanitize_name(audio.filename or 'upload')}.wav"
    dest.write_bytes(wav)
    return dest


@router.get("/jobs/{job_id}")
async def conversion_status(job_id: str):
    return voices.job_status(job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_conversion(job_id: str):
    return await voices.cancel_job(job_id)
