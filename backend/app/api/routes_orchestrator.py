from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import init_progress
from ..config import MODELS, yue2_specs
from ..orchestrator.manager import manager

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


class SwitchRequest(BaseModel):
    model: str


@router.get("/config")
async def get_config():
    return {
        "yue2_specs": yue2_specs(),
    }


@router.get("/status")
async def get_status():
    return manager.status_snapshot()


@router.get("/downloads")
async def get_downloads(model: str = "ace_step"):
    """What a model is pulling down right now, if anything.

    First-run initialization downloads several GB of checkpoints, and the only
    signal is the engine's own progress output - see init_progress.
    """
    if model not in MODELS:
        raise HTTPException(status_code=400, detail=f"unknown model '{model}'")
    log_name = MODELS[model].processes[0].name
    return {"downloads": init_progress.download_progress(log_name)}


@router.post("/switch")
async def switch(req: SwitchRequest):
    try:
        await manager.switch_to(req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        # Startup failed; manager.status_snapshot() already reflects the
        # per-model error state/message for the UI to display.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return manager.status_snapshot()


@router.post("/stop")
async def stop(model: Optional[str] = None):
    """Stop one engine, or every running engine when no model is named.

    Stopping "the active one" stopped everything back when only one could
    run; with both able to be up, a bare stop has to mean all of them or it
    silently leaves an engine holding the GPU.
    """
    try:
        if model is None:
            await manager.stop_all()
        else:
            await manager.stop_one(model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return manager.status_snapshot()
