"""Talking to the native audio.cpp server about what it currently holds.

Both the voice features and the separation lab need the same two things from
it: load a model on demand, and get everything out of VRAM before starting a
job that runs its own audiocpp_cli process outside the server's residency cap.
"""
from __future__ import annotations

import httpx

from .config import MODELS

BASE_URL = MODELS["yue2"].proxy_target


async def loaded_model_ids(client: httpx.AsyncClient) -> set[str]:
    listed = await client.get(f"{BASE_URL}/v1/models")
    return {m.get("id") for m in (listed.json().get("data") or []) if m.get("loaded")}


async def ensure_loaded(client: httpx.AsyncClient, spec: dict[str, str]) -> httpx.Response | None:
    """Load a model unless it's already resident. Returns the load response,
    or None when nothing needed doing."""
    if spec["id"] in await loaded_model_ids(client):
        return None
    return await client.post(
        f"{BASE_URL}/v1/models/load",
        json={
            "id": spec["id"],
            "path": spec["path"],
            "family": spec["family"],
            "task": spec["task"],
            "mode": spec["mode"],
            "load_options": {},
            "session_options": {},
        },
    )


async def release_vram(client: httpx.AsyncClient) -> None:
    """Unload everything the server holds.

    Jobs driven through audiocpp_cli get their own process and their own
    allocation, outside the residency cap the server enforces on its models -
    so on a 6-8 GB card, whatever the server is still holding is the
    difference between a job running and the allocation failing. The server
    reloads lazily on its next request, so this costs a reload, not state.
    """
    try:
        for model_id in await loaded_model_ids(client):
            await client.post(f"{BASE_URL}/v1/models/unload", json={"id": model_id})
    except httpx.HTTPError:
        pass  # best effort - worst case the job itself reports the OOM
