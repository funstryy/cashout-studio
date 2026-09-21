"""Multi-model source separation, in the shape UVR made familiar.

One model's separation is a guess; several models' separations disagree in
useful ways, and combining them per time-frequency bin is what an ensemble
buys you. Every model here runs through audiocpp_cli's `sep` task on the
Vulkan backend, so this is GPU work on any vendor - unlike the Demucs job in
stems.py, which stays on the CPU precisely because it's the quick one-click
path that has to coexist with a loaded model.

The two features are deliberately separate: stems.py is "split this track,
now"; this is the workbench where you choose models, ensemble them, and
audition the result before committing it to the track.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import numpy as np

from . import audio_buffers, db
from .audio_buffers import EnsembleAlgorithm
from .config import (
    AUDIOCPP_CLI_EXE,
    LOG_DIR,
    SEPARATION_MODELS,
    VOICE_BACKEND,
    YUE2_DEVICE,
    YUE2_DIR,
)
from .orchestrator.process import tail_log

IS_WINDOWS = sys.platform == "win32"

OutputFormat = Literal["wav", "flac", "mp3"]
StemFilter = Literal["all", "vocals", "instrumental"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

SAMPLE_MODE_SECONDS = 30.0

_job_lock = asyncio.Lock()


@dataclass
class SeparationJob:
    status: JobStatus
    models: list[str]
    algorithm: EnsembleAlgorithm
    step: str = ""
    progress: float = 0.0
    error: Optional[str] = None
    stems: dict[str, str] = field(default_factory=dict)
    track_id: Optional[int] = None
    proc: Optional[asyncio.subprocess.Process] = None
    cancel_requested: bool = False


_jobs: dict[str, SeparationJob] = {}


def model_installed(model_id: str) -> bool:
    return Path(SEPARATION_MODELS[model_id]["path"]).exists()


def available_models() -> list[dict]:
    """Every configured model, flagged with whether its weights are present -
    the UI lists them all so a missing download is visible rather than absent."""
    return [
        {
            "id": model["id"],
            "label": model["label"],
            "stems": model["stems"],
            "installed": model_installed(model["id"]),
        }
        for model in SEPARATION_MODELS.values()
    ]


def job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        return {"status": "unknown", "error": None, "stems": {}, "progress": 0.0, "step": ""}
    return {
        "status": job.status,
        "error": job.error,
        "step": job.step,
        "progress": job.progress,
        "stems": job.stems,
        "track_id": job.track_id,
        "models": job.models,
        "algorithm": job.algorithm,
    }


async def cancel(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job and job.status in ("queued", "running"):
        job.cancel_requested = True
        if job.proc is not None:
            await _kill_tree(job.proc)
    return job_status(job_id)


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
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


def start(
    *,
    track_id: int,
    models: list[str],
    algorithm: EnsembleAlgorithm,
    output_format: OutputFormat,
    stem_filter: StemFilter,
    sample_mode: bool,
    use_gpu: bool,
) -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = SeparationJob(status="queued", models=models, algorithm=algorithm, track_id=track_id)
    asyncio.create_task(
        _run(
            job_id,
            track_id=track_id,
            models=models,
            algorithm=algorithm,
            output_format=output_format,
            stem_filter=stem_filter,
            sample_mode=sample_mode,
            use_gpu=use_gpu,
        )
    )
    return job_id


async def _separate_one(job: SeparationJob, model_id: str, source: Path, work_dir: Path, use_gpu: bool) -> dict[str, Path]:
    model = SEPARATION_MODELS[model_id]
    out_dir = work_dir / model_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log_name = f"separation_{model_id}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    with open(LOG_DIR / f"{log_name}.log", "w", encoding="utf-8", errors="replace") as log_file:
        proc = await asyncio.create_subprocess_exec(
            str(AUDIOCPP_CLI_EXE),
            "--task", "sep",
            "--family", model["family"],
            "--model", model["path"],
            "--backend", VOICE_BACKEND if use_gpu else "cpu",
            "--device", YUE2_DEVICE,
            "--audio", str(source),
            "--out-dir", str(out_dir),
            cwd=str(YUE2_DIR),
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        job.proc = proc
        returncode = await proc.wait()
        job.proc = None

    if job.cancel_requested:
        return {}
    if returncode != 0:
        raise RuntimeError(f"{model['label']} exited with code {returncode}\n{tail_log(log_name)}")

    produced = {wav.stem.lower(): wav for wav in out_dir.glob("*.wav")}
    if not produced:
        raise RuntimeError(f"{model['label']} produced no stems\n{tail_log(log_name)}")
    return produced


async def _run(
    job_id: str,
    *,
    track_id: int,
    models: list[str],
    algorithm: EnsembleAlgorithm,
    output_format: OutputFormat,
    stem_filter: StemFilter,
    sample_mode: bool,
    use_gpu: bool,
) -> None:
    job = _jobs[job_id]
    work_dir = db.model_dir("separation") / job_id
    try:
        # Serialized against other separations: each model is a multi-GB
        # allocation of its own, and two at once is how a small card dies.
        async with _job_lock:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            job.status = "running"

            row = db.get_track(track_id)
            if not row:
                raise RuntimeError("track not found")
            work_dir.mkdir(parents=True, exist_ok=True)

            job.step = "preparing audio"
            source = await audio_buffers.prepare_source(
                Path(row["audio_path"]),
                work_dir / "source.wav",
                SAMPLE_MODE_SECONDS if sample_mode else None,
            )

            per_model: list[dict[str, Path]] = []
            for index, model_id in enumerate(models):
                job.step = f"separating with {SEPARATION_MODELS[model_id]['label']}"
                job.progress = index / (len(models) + 1)
                per_model.append(await _separate_one(job, model_id, source, work_dir, use_gpu))
                if job.cancel_requested:
                    job.status = "cancelled"
                    return

            job.step = "combining" if len(models) > 1 else "writing stems"
            job.progress = len(models) / (len(models) + 1)
            mixture = await audio_buffers.decode(source)
            stems = await _combine(per_model, mixture, algorithm)
            stems = _apply_filter(stems, stem_filter)

            out_dir = db.stems_dir(row["model"], track_id) if not sample_mode else work_dir / "out"
            if not sample_mode:
                shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)

            written: dict[str, str] = {}
            for name, audio in stems.items():
                written[name] = str(await audio_buffers.encode(audio, out_dir / name, output_format))

            # A sample run is a preview of settings, not the real separation -
            # it must not overwrite the stems the mixer and editor read.
            if not sample_mode:
                db.update_track_stems(track_id, written)
            job.stems = {name: f"/api/separation/jobs/{job_id}/stems/{name}" for name in written}
            job._paths = written  # type: ignore[attr-defined]
            job.progress = 1.0
            job.step = "done"
            job.status = "done"
    except Exception as exc:  # noqa: BLE001 - any failure must surface to the UI
        job.status = "failed"
        job.error = str(exc)
    finally:
        if job.status in ("failed", "cancelled"):
            shutil.rmtree(work_dir, ignore_errors=True)


async def _combine(
    per_model: list[dict[str, Path]],
    mixture: np.ndarray,
    algorithm: EnsembleAlgorithm,
) -> dict[str, np.ndarray]:
    """Decode every model's stems and ensemble the ones they share.

    Models don't agree on what they produce - the RoFormers give vocals (and
    an instrumental derived from it), HTDemucs gives four parts - so stems are
    combined by name, and a stem only one model produced passes through as-is.
    """
    decoded: list[dict[str, np.ndarray]] = []
    for produced in per_model:
        decoded.append({name: await audio_buffers.decode(path) for name, path in produced.items()})

    names: list[str] = []
    for stems in decoded:
        for name in stems:
            if name not in names:
                names.append(name)

    combined: dict[str, np.ndarray] = {}
    take_counts: dict[str, int] = {}
    for name in names:
        takes = [stems[name] for stems in decoded if name in stems]
        take_counts[name] = len(takes)
        combined[name] = audio_buffers.ensemble(takes, algorithm)

    # Derive the instrumental from the vocals we actually ended up with, so
    # the pair still sums back to the mixture. Ensembling vocals across models
    # changes them; an instrumental that came from only one of those models
    # would no longer be their complement, and layering the two would leave
    # audible residue of whatever the ensemble moved.
    if "vocals" in combined and (take_counts.get("vocals", 0) > 1 or "instrumental" not in combined):
        combined["instrumental"] = audio_buffers.invert(mixture, combined["vocals"])
    return combined


def _apply_filter(stems: dict[str, np.ndarray], stem_filter: StemFilter) -> dict[str, np.ndarray]:
    if stem_filter == "all":
        return stems
    return {name: audio for name, audio in stems.items() if name == stem_filter}


def job_stem_path(job_id: str, name: str) -> Optional[Path]:
    job = _jobs.get(job_id)
    paths = getattr(job, "_paths", None) if job else None
    if not paths or name not in paths:
        return None
    return Path(paths[name])
