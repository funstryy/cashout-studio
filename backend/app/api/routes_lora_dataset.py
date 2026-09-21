"""Bulk file upload for ACE-Step LoRA training datasets.

ACE-Step's own training API (see external/patches, /v1/dataset/*) only ever
scans a server-local folder path - it has no upload endpoint at all. This
lets the browser drop/pick files directly instead of the user having to copy
them onto the server's disk by hand first. Uploaded files land under
ACE_STEP_DIR/datasets/<name>/, which is exactly the location ACE-Step's own
path-safety check (safe_path()) already requires audio_dir to be inside, so
the resulting relative path can be handed straight to /v1/dataset/scan.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import audio_buffers
from ..audio_io import AudioConversionError
from ..config import ACE_STEP_DIR

router = APIRouter(prefix="/api/lora-dataset", tags=["lora-dataset"])

ALLOWED_AUDIO_EXT = {"wav", "mp3", "flac", "ogg", "opus"}


def _sanitize_dataset_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "")[:60]).strip("_")
    return name or "my_lora_dataset"


def _unique_dest(target_dir: Path, filename: str) -> Path:
    stem, _, ext = filename.rpartition(".")
    dest = target_dir / filename
    n = 1
    while dest.exists():
        dest = target_dir / f"{stem}_{n}.{ext}"
        n += 1
    return dest


ORIGINALS_DIR = "_originals"


@router.post("/trim")
async def trim_dataset(
    dataset_name: str = Form(...),
    seconds: float = Form(30.0),
    start: float = Form(0.0),
    only: Optional[str] = Form(None),
):
    """Cut every clip in a dataset down to its first N seconds.

    Training cost is driven by how long each sample is: a full song
    preprocesses into a ~3900-frame latent sequence, and the transformer's
    attention over that dominates every step. Thirty-second clips are roughly
    an eighth of the length, which is the difference between a run measured in
    days and one measured in hours.

    Clips are trimmed in place so the dataset's labels - which are keyed by
    filename - keep pointing at them. The untouched originals move to
    _originals/ so this stays reversible and re-runnable.
    """
    if seconds <= 0:
        raise HTTPException(status_code=400, detail="seconds must be greater than 0")

    safe_name = _sanitize_dataset_name(dataset_name)
    dataset_dir = ACE_STEP_DIR / "datasets" / safe_name
    if not dataset_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"dataset '{dataset_name}' not found")

    # Deliberately a sibling of the dataset, not a subfolder of it: the
    # dataset scan walks recursively, so originals kept inside would come
    # back as extra samples and every clip would be trained on twice - once
    # short, once full length.
    originals_dir = ACE_STEP_DIR / "datasets" / ORIGINALS_DIR / safe_name
    originals_dir.mkdir(parents=True, exist_ok=True)

    # `only` re-cuts a subset - songs usually want a later start than beats,
    # so they get their own pass rather than one window for the whole folder.
    wanted = {name.strip() for name in only.split("|")} if only else None

    trimmed: list[str] = []
    failed: list[str] = []
    for audio in sorted(dataset_dir.iterdir()):
        if not audio.is_file() or audio.suffix.lstrip(".").lower() not in ALLOWED_AUDIO_EXT:
            continue
        if wanted is not None and audio.name not in wanted:
            continue
        # Re-trimming works from the pristine copy, so lowering the limit
        # twice doesn't compound into an ever-shorter clip.
        original = originals_dir / audio.name
        if not original.exists():
            shutil.move(str(audio), str(original))
        try:
            audio.unlink(missing_ok=True)
            await audio_buffers.trim_file(original, audio, seconds, start)
            trimmed.append(audio.name)
        except AudioConversionError as exc:
            # Put the clip back rather than leaving the dataset short a file.
            shutil.copy2(original, audio)
            failed.append(f"{audio.name}: {exc}")

    return {
        "dataset": dataset_dir.name,
        "seconds": seconds,
        "trimmed": trimmed,
        "failed": failed,
        "originals_dir": str(originals_dir),
    }


@router.post("/upload")
async def upload_dataset_files(
    dataset_name: str = Form(...),
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    safe_name = _sanitize_dataset_name(dataset_name)
    target_dir = ACE_STEP_DIR / "datasets" / safe_name
    target_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    skipped: list[str] = []
    for f in files:
        filename = Path(f.filename or "").name  # drop any path components
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or ext not in ALLOWED_AUDIO_EXT:
            skipped.append(f.filename or "(unnamed)")
            continue
        dest = _unique_dest(target_dir, filename)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(dest.name)

    if not saved:
        raise HTTPException(
            status_code=400,
            detail=f"No supported audio files in upload. Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXT))}",
        )

    return {
        "audio_dir": f"datasets/{safe_name}",
        "saved": saved,
        "skipped": skipped,
    }
