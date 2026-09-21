"""Home-made plugins: the recipes, not the audio.

A custom plugin is a chain of Web Audio primitives and their parameter
values - a few hundred bytes of JSON. It is stored per profile because an
effect someone built is their work, the same as a track or a project.

Unlike the VST inserts these run live in the signal path, so nothing here
ever touches a file; the chain is rebuilt in the browser every time it is
applied. See frontend/src/audio/customEffects.ts.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..config import DATA_DIR
from .routes_users import current_user_id

router = APIRouter(prefix="/api/effects", tags=["effects"])


def _store(user_id: int) -> Path:
    directory = DATA_DIR / "effects"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"user{user_id}.json"


def _read(user_id: int) -> list[dict[str, Any]]:
    path = _store(user_id)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        # A corrupt file should cost the user their effects list, not the
        # ability to open the app.
        return []


def _write(user_id: int, plugins: list[dict[str, Any]]) -> None:
    _store(user_id).write_text(json.dumps(plugins, indent=2, ensure_ascii=False), encoding="utf-8")


@router.get("")
async def list_effects(user_id: int = Depends(current_user_id)):
    return {"plugins": _read(user_id)}


@router.post("")
async def save_effect(body: dict = Body(...), user_id: int = Depends(current_user_id)):
    name = (body.get("name") or "").strip()[:60]
    chain = body.get("chain")
    if not name:
        raise HTTPException(status_code=400, detail="a plugin needs a name")
    if not isinstance(chain, list) or not chain:
        raise HTTPException(status_code=400, detail="a plugin needs at least one effect")

    plugins = _read(user_id)
    plugin_id = (body.get("id") or "").strip() or uuid.uuid4().hex
    record = {
        "id": plugin_id,
        "name": name,
        "chain": chain,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # Saving over an existing id is an edit, not a duplicate.
    for index, existing in enumerate(plugins):
        if existing.get("id") == plugin_id:
            plugins[index] = record
            break
    else:
        plugins.append(record)

    _write(user_id, plugins)
    return record


@router.delete("/{plugin_id}")
async def delete_effect(plugin_id: str, user_id: int = Depends(current_user_id)):
    plugins = _read(user_id)
    remaining = [p for p in plugins if p.get("id") != plugin_id]
    if len(remaining) == len(plugins):
        raise HTTPException(status_code=404, detail="no such plugin")
    _write(user_id, remaining)
    return {"deleted": True}
