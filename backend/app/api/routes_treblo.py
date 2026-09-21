"""Treblo's Melodia API, proxied.

Treblo publishes a real developer API, so this is an ordinary integration:
the user brings their own key, every call is theirs, and nothing is worked
around. The free tier is 1,500 credits on sign-up and a v3 generation costs
100, so a new key is fifteen songs before anything is owed.

The key stays on this side rather than in the browser, for two reasons. It
never appears in a page the user could accidentally screenshot or in a
devtools network pane, and the proxy sidesteps CORS - api.treblo.com has no
reason to allow requests from a loopback origin.

Endpoints mirrored from https://treblo.com/developers/docs:
    POST /v1/generations/v3            -> { task_id }
    GET  /v1/generations/status/{id}   -> { status: PENDING|SUCCESS|FAILURE }
    GET  /v1/generations/{id}          -> { song_paths: [...] }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException

from ..config import DATA_DIR
from .routes_users import current_user_id

router = APIRouter(prefix="/api/treblo", tags=["treblo"])

API_BASE = "https://api.treblo.com/v1"
# Every v3 request costs this, per Treblo's pricing page. Shown in the UI so
# the free tier's fifteen songs are not a surprise when they run out.
CREDITS_PER_GENERATION = 100


def _key_store(user_id: int) -> Path:
    directory = DATA_DIR / "keys"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"treblo_user{user_id}.json"


def _read_key(user_id: int) -> Optional[str]:
    path = _key_store(user_id)
    if not path.is_file():
        return None
    try:
        return (json.loads(path.read_text(encoding="utf-8")) or {}).get("api_key") or None
    except (OSError, ValueError):
        return None


def _require_key(user_id: int) -> str:
    key = _read_key(user_id)
    if not key:
        raise HTTPException(status_code=401, detail="add your Treblo API key first")
    return key


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def _request(method: str, path: str, key: str, payload: Any = None) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.request(
            method, f"{API_BASE}{path}", headers=_headers(key),
            json=payload if payload is not None else None,
        )
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Treblo rejected that API key")
    if response.status_code == 402:
        raise HTTPException(status_code=402, detail="out of Treblo credits")
    if response.status_code >= 400:
        detail = response.text[:300]
        try:
            body = response.json()
            detail = body.get("detail") or body.get("error") or detail
        except ValueError:
            pass
        raise HTTPException(status_code=502, detail=f"Treblo: {detail}")
    try:
        return response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Treblo returned a non-JSON response") from None


@router.get("/status")
async def status(user_id: int = Depends(current_user_id)):
    key = _read_key(user_id)
    return {
        "configured": bool(key),
        # Never the key itself - just enough to recognise which one is saved.
        "key_hint": f"…{key[-4:]}" if key and len(key) > 4 else None,
        "credits_per_song": CREDITS_PER_GENERATION,
    }


@router.post("/key")
async def save_key(api_key: str = Body(..., embed=True), user_id: int = Depends(current_user_id)):
    clean = (api_key or "").strip()
    if len(clean) < 12:
        raise HTTPException(status_code=400, detail="that does not look like a Treblo API key")
    _key_store(user_id).write_text(json.dumps({"api_key": clean}), encoding="utf-8")
    return await status(user_id)


@router.delete("/key")
async def clear_key(user_id: int = Depends(current_user_id)):
    _key_store(user_id).unlink(missing_ok=True)
    return await status(user_id)


@router.post("/generate")
async def generate(body: dict = Body(...), user_id: int = Depends(current_user_id)):
    """Starts a v3 generation and hands back the task id.

    Only the fields the user actually set are forwarded. Treblo's own
    guidance is to send a prompt alone and let the model infer tags, lyrics
    and strengths - passing empty arrays or default strengths measurably
    degrades the result, so an unset control must mean absent, not zero.
    """
    key = _require_key(user_id)

    prompt = (body.get("prompt") or "").strip()
    lyrics = (body.get("lyrics") or "").strip()
    instrumental = bool(body.get("instrumental"))
    if not prompt and not lyrics:
        raise HTTPException(status_code=400, detail="describe the song, or give it lyrics")

    payload: dict[str, Any] = {"prompt": prompt}
    # Instrumental and lyrics are mutually exclusive in Treblo's contract.
    if instrumental:
        payload["instrumental"] = True
    elif lyrics:
        payload["lyrics"] = lyrics

    output_format = (body.get("output_format") or "").strip()
    if output_format in ("mp3", "flac", "wav", "ogg", "m4a"):
        payload["output_format"] = output_format

    length = body.get("length_range")
    if isinstance(length, list) and len(length) == 2:
        payload["length_range"] = [int(length[0]), int(length[1])]

    negative = [str(tag).strip() for tag in (body.get("negative_tags") or []) if str(tag).strip()]
    if negative:
        payload["negative_tags"] = negative

    # style_scale defaults to 4.5 and prompt_strength to 1.0, and exactly one
    # of them may exceed 1.0. Only send a value when it was deliberately
    # changed, so the defaults stay coherent.
    style_scale = body.get("style_scale")
    if isinstance(style_scale, (int, float)) and abs(float(style_scale) - 4.5) > 0.01:
        payload["style_scale"] = float(style_scale)

    return await _request("POST", "/generations/v3", key, payload)


@router.get("/status/{task_id}")
async def generation_status(task_id: str, user_id: int = Depends(current_user_id)):
    return await _request("GET", f"/generations/status/{task_id}", _require_key(user_id))


@router.get("/result/{task_id}")
async def generation_result(task_id: str, user_id: int = Depends(current_user_id)):
    return await _request("GET", f"/generations/{task_id}", _require_key(user_id))


@router.post("/import")
async def import_song(body: dict = Body(...), user_id: int = Depends(current_user_id)):
    """Pulls a finished song into the library.

    Downloaded server-side rather than fetched by the page: the audio lands
    as an ordinary library track, so it is immediately available to the DAW,
    the separation lab and the LoRA dataset builder without an export step.
    That is what makes this feel like part of the studio rather than a tab
    bolted onto it.
    """
    from .. import db
    from ..audio_io import AudioConversionError

    url = (body.get("url") or "").strip()
    title = (body.get("title") or "Treblo song").strip()[:80]
    if not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="that is not a Treblo audio URL")

    _require_key(user_id)   # only a configured profile may import

    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"could not download it: {exc}") from None

    suffix = Path(url.split("?")[0]).suffix.lower() or ".ogg"
    if suffix not in (".mp3", ".wav", ".flac", ".ogg", ".m4a"):
        suffix = ".ogg"

    out_dir = db.model_dir("treblo")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in title if ch.isalnum() or ch in " _-").strip() or "treblo"
    destination = out_dir / f"{safe[:60]}{suffix}"
    index = 1
    while destination.exists():
        destination = out_dir / f"{safe[:60]}_{index}{suffix}"
        index += 1
    destination.write_bytes(response.content)

    try:
        track_id = db.insert_track(
            model="treblo",
            title=title,
            lyrics=body.get("lyrics") or "",
            seed=None,
            duration_ms=None,
            wall_ms=None,
            params={"source": "treblo", "task_id": body.get("task_id"), "url": url},
            audio_path=destination,
            abc_path=None,
        )
    except (OSError, AudioConversionError) as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return {"track_id": track_id, "audio_url": f"/api/tracks/{track_id}/audio", "title": title}
