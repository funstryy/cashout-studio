"""On-demand stem separation (Demucs) for a saved track.

Unlike ACE-Step/YuE2, Demucs isn't a persistent HTTP server tracked in
MODELS/OrchestratorState - it's a one-shot CLI job. This module keeps its own
tiny in-memory job registry and drives the subprocess directly.

Runs alongside whatever model (if any) is currently active rather than
stopping it first, but deliberately stays out of that model's way:

  - It runs on the CPU (DEMUCS_DEVICE). The torch build installed for AMD/Windows
    is CPU-only anyway (see setup_models.ps1), and keeping it there means a
    separation never competes for VRAM with an active YuE2/ACE-Step - which
    matters on a 6-8 GB card where YuE2-3B alone is most of the budget.
  - It is capped to a share of the CPU (DEMUCS_THREADS). Uncapped, torch
    takes a thread per physical core, and a separation started mid-generation
    makes the whole app - including the UI's own request handling - crawl.

`_job_lock` below additionally serializes multiple *Demucs* jobs against
each other, independent of orchestrator.manager's model-switching lock.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from . import db
from .config import DEMUCS_DEVICE, DEMUCS_DIR, DEMUCS_EXE, DEMUCS_THREADS, FFMPEG_BIN_DIR, LOG_DIR
from .orchestrator.process import tail_log

IS_WINDOWS = sys.platform == "win32"

STEM_NAMES = ("vocals", "drums", "bass", "other")

_job_lock = asyncio.Lock()

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


@dataclass
class StemJob:
    status: JobStatus
    error: Optional[str] = None
    proc: Optional[asyncio.subprocess.Process] = None
    cancel_requested: bool = False


_jobs: dict[int, StemJob] = {}


async def start(track_id: int, *, force: bool = False) -> StemJob:
    job = _jobs.get(track_id)
    if job and job.status in ("queued", "running"):
        return job
    if job and job.status == "done" and not force:
        return job
    job = StemJob(status="queued")
    _jobs[track_id] = job
    asyncio.create_task(_run(track_id))
    return job


async def cancel(track_id: int) -> dict:
    job = _jobs.get(track_id)
    if job and job.status in ("queued", "running"):
        job.cancel_requested = True
        if job.proc is not None:
            await _kill_tree(job.proc)
    return status(track_id)


def is_active(track_id: int) -> bool:
    job = _jobs.get(track_id)
    return bool(job and job.status in ("queued", "running"))


def forget(track_id: int) -> None:
    """Drop any in-memory job record so status() falls back to the DB
    (used after deleting stems out from under a finished job)."""
    _jobs.pop(track_id, None)


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    # /T: demucs itself forks worker processes for the separation, which a
    # plain kill() of the launched interpreter would leave running.
    # Mirrors ManagedProcess._force_kill().
    if IS_WINDOWS:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(proc.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def status(track_id: int) -> dict:
    # A known job always wins over the DB: while a (re)run is in flight the
    # old stems_json may still point at files _run() is about to delete, so
    # trusting the DB here would misreport "done" for a cancelled/failed
    # redo. The DB is only consulted as a fallback once there's no job left
    # in memory (e.g. after a backend restart).
    job = _jobs.get(track_id)
    if job:
        return {"status": job.status, "error": job.error}
    row = db.get_track(track_id)
    if row and row["stems_json"]:
        return {"status": "done", "error": None}
    return {"status": "idle", "error": None}


async def _run(track_id: int) -> None:
    job = _jobs[track_id]
    log_name = f"demucs_{track_id}"
    try:
        async with _job_lock:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            job.status = "running"

            row = db.get_track(track_id)
            if not row:
                job.status = "failed"
                job.error = "track not found"
                return
            audio_path = Path(row["audio_path"])
            out_dir = db.stems_dir(row["model"], track_id)
            shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            # Invalidate any previous run's stems_json now, since its files
            # were just deleted - the DB must never point at files that no
            # longer exist, even if this run itself fails or is cancelled.
            db.update_track_stems(track_id, None)

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"{log_name}.log"
            env = os.environ.copy()
            env["PATH"] = f"{FFMPEG_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
            # torch reads these at import time; -d cpu alone would still let
            # it spin up one thread per core.
            env["OMP_NUM_THREADS"] = str(DEMUCS_THREADS)
            env["MKL_NUM_THREADS"] = str(DEMUCS_THREADS)

            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                proc = await asyncio.create_subprocess_exec(
                    str(DEMUCS_EXE),
                    "-n", "htdemucs", "-d", DEMUCS_DEVICE,
                    "-o", str(out_dir), str(audio_path),
                    cwd=str(DEMUCS_DIR),
                    env=env,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
                job.proc = proc
                returncode = await proc.wait()

            if job.cancel_requested:
                job.status = "cancelled"
                return
            if returncode != 0:
                job.status = "failed"
                job.error = f"demucs exited with code {returncode}\n{tail_log(log_name)}"
                return

            stems = {
                wav.stem: str(wav)
                for wav in out_dir.rglob("*.wav")
                if wav.stem in STEM_NAMES
            }
            if not stems:
                job.status = "failed"
                job.error = f"demucs finished but produced no stem files\n{tail_log(log_name)}"
                return
            db.update_track_stems(track_id, stems)
            job.status = "done"
    except Exception as exc:  # noqa: BLE001 - any failure must surface to the UI
        job.status = "failed"
        job.error = str(exc)
