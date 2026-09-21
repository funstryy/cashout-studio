"""Imported reference voices, and the two things you can do with one.

A "voice" here is just a short recording the user imports - no training runs.
Both engines this module drives are zero-shot: they read the reference clip at
inference time and adapt to it, which is what makes voices usable on a card
that could never finish a fine-tune.

  - Speaking (Chatterbox, task `clon`) happens inside audiocpp_server, which
    is handed this folder as its --voice-dir - so a clip saved here is
    immediately selectable as `voice` in a /v1/audio/speech request.
  - Conversion (Seed-VC, tasks `vc` for speech and `svc` for singing) has no
    HTTP route in that server, so it runs as a one-shot audiocpp_cli job,
    the same pattern stems.py uses for Demucs.

The library is a plain folder of `<name>.wav` files plus a `prompt_text` file
of `<name>|<transcript>` lines - the exact layout audiocpp_server already
reads, rather than a parallel index of our own that could drift from it.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from . import audio_buffers, db
from .audio_io import to_wav
from .config import (
    AUDIOCPP_CLI_EXE,
    LOG_DIR,
    VOICE_BACKEND,
    VOICES_DIR,
    YUE2_DEVICE,
    YUE2_DIR,
    voice_specs,
)
from .orchestrator.process import tail_log

IS_WINDOWS = sys.platform == "win32"

# Reference clips are stored mono at 24 kHz: both engines resample to their
# own rate anyway, and a stereo 48 kHz copy of a 10-second clip is pure bulk.
VOICE_SAMPLE_RATE = 24000
PROMPT_TEXT_FILE = "prompt_text"

ConversionTask = Literal["vc", "svc"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

_job_lock = asyncio.Lock()


@dataclass
class VoiceJob:
    status: JobStatus
    voice: str
    error: Optional[str] = None
    track_id: Optional[int] = None
    proc: Optional[asyncio.subprocess.Process] = None
    cancel_requested: bool = False


_jobs: dict[str, VoiceJob] = {}


def sanitize_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "")[:48]).strip("_")
    return name or "voice"


def _library_dir() -> Path:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    return VOICES_DIR


def _read_transcripts() -> dict[str, str]:
    path = _library_dir() / PROMPT_TEXT_FILE
    if not path.exists():
        return {}
    transcripts: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        name, sep, text = line.partition("|")
        if sep:
            transcripts[name.strip()] = text.strip()
    return transcripts


def _write_transcripts(transcripts: dict[str, str]) -> None:
    path = _library_dir() / PROMPT_TEXT_FILE
    lines = [f"{name}|{text}" for name, text in sorted(transcripts.items()) if text]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def list_voices() -> list[dict]:
    transcripts = _read_transcripts()
    voices = []
    for wav in sorted(_library_dir().glob("*.wav")):
        name = wav.stem
        voices.append(
            {
                "name": name,
                "transcript": transcripts.get(name, ""),
                "size_bytes": wav.stat().st_size,
                "audio_url": f"/api/voices/{name}/audio",
            }
        )
    return voices


def voice_path(name: str) -> Optional[Path]:
    safe = sanitize_name(name)
    path = _library_dir() / f"{safe}.wav"
    return path if path.exists() else None


async def save_voice(*, name: str, data: bytes, transcript: str = "") -> dict:
    safe = sanitize_name(name)
    wav = await to_wav(data, sample_rate=VOICE_SAMPLE_RATE, channels=1)
    (_library_dir() / f"{safe}.wav").write_bytes(wav)

    transcripts = _read_transcripts()
    # The transcript is what the engine conditions on alongside the audio;
    # an empty one is valid (the models fall back to audio-only reference),
    # so clearing it has to actually remove the stale line.
    if transcript.strip():
        transcripts[safe] = transcript.strip().replace("\n", " ")
    else:
        transcripts.pop(safe, None)
    _write_transcripts(transcripts)

    return {"name": safe, "transcript": transcripts.get(safe, ""), "audio_url": f"/api/voices/{safe}/audio"}


def delete_voice(name: str) -> bool:
    path = voice_path(name)
    if not path:
        return False
    path.unlink()
    transcripts = _read_transcripts()
    if transcripts.pop(path.stem, None) is not None:
        _write_transcripts(transcripts)
    return True


def job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        return {"status": "unknown", "error": None, "track_id": None}
    return {"status": job.status, "error": job.error, "track_id": job.track_id, "voice": job.voice}


async def cancel_job(job_id: str) -> dict:
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


def start_conversion(*, source: Path, voice: str, task: ConversionTask, title: str) -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = VoiceJob(status="queued", voice=voice)
    asyncio.create_task(_run_conversion(job_id, source=source, voice=voice, task=task, title=title))
    return job_id


async def _run_conversion(job_id: str, *, source: Path, voice: str, task: ConversionTask, title: str) -> None:
    job = _jobs[job_id]
    log_name = f"voice_{job_id}"
    try:
        # One conversion at a time: each run loads Seed-VC onto the GPU in its
        # own process, outside the residency budget audiocpp_server enforces
        # for its own models, so two at once is how you exhaust a small card.
        async with _job_lock:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            job.status = "running"

            reference = voice_path(voice)
            if not reference:
                job.status = "failed"
                job.error = f"voice '{voice}' not found"
                return
            if not AUDIOCPP_CLI_EXE.exists():
                job.status = "failed"
                job.error = (
                    f"audiocpp_cli not found at {AUDIOCPP_CLI_EXE}. Rebuild audio.cpp with "
                    "-Target audiocpp_cli (see setup_models.ps1)."
                )
                return

            out_dir = db.model_dir("voices")
            out_path = out_dir / f"{job_id}.wav"
            # audiocpp_cli only reads WAV, and library tracks are just as
            # likely to be mp3 or flac - convert before handing it over.
            source = await audio_buffers.prepare_source(source, out_dir / f"{job_id}_source.wav")
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = LOG_DIR / f"{log_name}.log"

            spec = voice_specs()["seed_vc"]
            with open(log_path, "w", encoding="utf-8", errors="replace") as log_file:
                proc = await asyncio.create_subprocess_exec(
                    str(AUDIOCPP_CLI_EXE),
                    "--task", task,
                    "--family", spec["family"],
                    "--model", spec["path"],
                    "--backend", VOICE_BACKEND,
                    "--device", YUE2_DEVICE,
                    "--audio", str(source),
                    "--voice-ref", str(reference),
                    "--out", str(out_path),
                    cwd=str(YUE2_DIR),
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
                job.proc = proc
                returncode = await proc.wait()

            if job.cancel_requested:
                job.status = "cancelled"
                out_path.unlink(missing_ok=True)
                return
            if returncode != 0 or not out_path.exists():
                job.status = "failed"
                job.error = f"voice conversion exited with code {returncode}\n{tail_log(log_name)}"
                return

            job.track_id = db.insert_track(
                model="voices",
                title=title or f"{voice} conversion",
                lyrics="",
                seed=None,
                duration_ms=None,
                wall_ms=None,
                params={"voice": voice, "task": task, "source": str(source)},
                audio_path=out_path,
                abc_path=None,
            )
            job.status = "done"
    except Exception as exc:  # noqa: BLE001 - any failure must surface to the UI
        job.status = "failed"
        job.error = str(exc)
