"""Machine telemetry, measured rather than decorative. See system_stats.py."""
from __future__ import annotations

from fastapi import APIRouter

from .. import system_stats

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("")
async def system_snapshot():
    return await system_stats.snapshot()
