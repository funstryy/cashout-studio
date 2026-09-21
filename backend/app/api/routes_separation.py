"""Separation lab endpoints: pick models, ensemble them, get stems back.

Unlike /api/tracks/{id}/stems - the one-click Demucs job - this is the
deliberate path: several models, a combining algorithm, and a sample mode for
auditioning settings before spending minutes on a whole song.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import audiocpp, db, separation
from ..config import SEPARATION_MODELS
from ..orchestrator.manager import manager
from ..orchestrator.state import ModelStatus
from .routes_proxy import _client

router = APIRouter(prefix="/api/separation", tags=["separation"])


class StartRequest(BaseModel):
    track_id: int
    models: list[str] = Field(min_length=1)
    algorithm: Literal["max_spec", "min_spec", "average"] = "max_spec"
    output_format: Literal["wav", "flac", "mp3"] = "wav"
    stem_filter: Literal["all", "vocals", "instrumental"] = "all"
    sample_mode: bool = False
    use_gpu: bool = True


@router.get("/models")
async def get_models():
    return {"models": separation.available_models()}


@router.post("/start")
async def start(req: StartRequest = Body(...)):
    unknown = [m for m in req.models if m not in SEPARATION_MODELS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown model(s): {', '.join(unknown)}")
    missing = [m for m in req.models if not separation.model_installed(m)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"weights not downloaded for: {', '.join(missing)}. Run setup_models.ps1.",
        )
    if not db.get_track(req.track_id):
        raise HTTPException(status_code=404, detail="track not found")

    # These models run in their own audiocpp_cli process, so anything the
    # engine server still holds is competing with them for the same VRAM.
    if manager.state.models["yue2"].status == ModelStatus.RUNNING:
        await audiocpp.release_vram(_client)

    job_id = separation.start(
        track_id=req.track_id,
        models=req.models,
        algorithm=req.algorithm,
        output_format=req.output_format,
        stem_filter=req.stem_filter,
        sample_mode=req.sample_mode,
        use_gpu=req.use_gpu,
    )
    return {"job_id": job_id, **separation.job_status(job_id)}


@router.get("/jobs/{job_id}")
async def status(job_id: str):
    return separation.job_status(job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    return await separation.cancel(job_id)


@router.get("/jobs/{job_id}/stems/{name}")
async def stem_file(job_id: str, name: str):
    path = separation.job_stem_path(job_id, name)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="stem not found")
    return FileResponse(path, filename=path.name)
