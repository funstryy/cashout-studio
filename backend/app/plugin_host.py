"""Driving the out-of-process VST3 host.

Plugins are native code and cannot run in the browser, so a track's effect
chain is *printed* rather than monitored live: the audio is rendered through
the plugin here and comes back as a new take. That is the honest shape of
plugin support in an app whose playback engine is Web Audio - live insert
monitoring would mean moving the whole engine out of the browser.

Audio is handed over as raw interleaved float32 because ffmpeg already
normalises every format on this side; the host never parses a container.

Settings are made in the plugin's own window - the host opens a real Windows
window and hands the plugin its handle - and kept in a state file per insert
slot. Rebuilding a plugin's interface out of its parameter list gives a column
of numbered sliders, which is unusable for anything with a graph, a keyboard
or a preset browser, and is not how any DAW does it.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from . import audio_buffers, db
from .audio_buffers import CHANNELS, SAMPLE_RATE
from .config import LOG_DIR, VST_HOST_EXE

IS_WINDOWS = sys.platform == "win32"

# The host prints JSON to stdout, so it is a console program; without this a
# console window would flash up beside every plugin window.
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

_job_lock = asyncio.Lock()


@dataclass
class PluginJob:
    status: JobStatus
    plugin: str
    error: Optional[str] = None
    track_id: Optional[int] = None
    proc: Optional[asyncio.subprocess.Process] = None
    cancel_requested: bool = False
    stems: dict = field(default_factory=dict)


@dataclass
class EditorJob:
    status: JobStatus
    plugin: str
    state_key: str
    error: Optional[str] = None
    proc: Optional[asyncio.subprocess.Process] = None


_jobs: dict[str, PluginJob] = {}
_editors: dict[str, EditorJob] = {}


def state_path(state_key: str) -> Path:
    """Where one insert slot's settings live.

    The key names a slot - a track and a plugin - so reopening the window
    finds the plugin exactly as it was left. It arrives over HTTP, so it is
    scrubbed down to something that can only be a filename.
    """
    directory = db.model_dir("plugins") / "state"
    directory.mkdir(parents=True, exist_ok=True)
    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", state_key)[:120] or "default"
    return directory / f"{clean}.vstate"


def has_state(state_key: str) -> bool:
    return state_path(state_key).exists()


def editor_status(job_id: str) -> dict:
    job = _editors.get(job_id)
    if not job:
        return {"status": "unknown", "error": None, "state_key": None}
    return {"status": job.status, "error": job.error, "state_key": job.state_key, "plugin": job.plugin}


def open_editor(*, plugin_path: str, state_key: str) -> str:
    """Show the plugin's own interface and return a job to poll.

    Deliberately outside the render lock: a window stays open for as long as
    someone is working in it, and holding that lock meanwhile would stop every
    other plugin print on the machine.
    """
    job_id = uuid.uuid4().hex
    _editors[job_id] = EditorJob(status="queued", plugin=Path(plugin_path).stem, state_key=state_key)
    asyncio.create_task(_run_editor(job_id, plugin_path=plugin_path, state_key=state_key))
    return job_id


async def close_editor(job_id: str) -> dict:
    """Close the window from the studio side - for a plugin that opened off
    the edge of the desktop, or one whose own close button stopped responding."""
    job = _editors.get(job_id)
    if job and job.proc is not None and IS_WINDOWS:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(job.proc.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    return editor_status(job_id)


def _log_diagnostics(job_id: str, diagnostics: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / f"plugin_editor_{job_id}.log").write_text(diagnostics, encoding="utf-8", errors="replace")
    except OSError:
        pass


async def _run_editor(job_id: str, *, plugin_path: str, state_key: str) -> None:
    job = _editors[job_id]
    target = state_path(state_key)
    try:
        if not host_available():
            raise RuntimeError(f"VST host not built at {VST_HOST_EXE}")
        job.status = "running"
        proc = await asyncio.create_subprocess_exec(
            str(VST_HOST_EXE), "edit", "--plugin", plugin_path, "--state", str(target),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        job.proc = proc
        stdout, stderr = await proc.communicate()
        job.proc = None

        text = stdout.decode("utf-8", "replace").strip()
        diagnostics = stderr.decode("utf-8", "replace").strip()
        result: dict = {}
        if text:
            try:
                result = json.loads(text.splitlines()[-1])
            except json.JSONDecodeError:
                result = {}

        if result.get("ok"):
            job.status = "done"
        elif "[crash]" in diagnostics:
            # The plugin faulted inside its own code. Worth saying plainly:
            # the point of hosting out of process is that this costs the user
            # one window rather than the studio, and the trace names the
            # library at fault rather than leaving a window that never opened.
            job.status = "failed"
            job.error = (
                f"{job.plugin} crashed while opening its window. "
                "It is the plugin that faulted, not the studio - nothing else was lost.\n\n"
                + diagnostics[-600:]
            )
            _log_diagnostics(job_id, diagnostics)
        elif not text:
            # Killed from the studio side. The state file keeps whatever it
            # held before the window opened.
            job.status = "cancelled"
        else:
            job.status = "failed"
            job.error = result.get("error") or diagnostics[:400] or "the plugin editor closed unexpectedly"
    except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
        job.status = "failed"
        job.error = str(exc)


def host_available() -> bool:
    return VST_HOST_EXE.exists()


async def describe(plugin_path: str) -> dict:
    """Name, vendor, bus counts and parameters, straight from the plugin."""
    if not host_available():
        return {"ok": False, "error": f"VST host not built at {VST_HOST_EXE}"}
    proc = await asyncio.create_subprocess_exec(
        str(VST_HOST_EXE), "scan", "--plugin", plugin_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    text = stdout.decode("utf-8", "replace").strip()
    if not text:
        return {"ok": False, "error": stderr.decode("utf-8", "replace").strip()[:400] or "host produced no output"}
    try:
        return json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": text[:400]}


def job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        return {"status": "unknown", "error": None, "track_id": None}
    return {"status": job.status, "error": job.error, "track_id": job.track_id, "plugin": job.plugin}


async def cancel(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job and job.status in ("queued", "running"):
        job.cancel_requested = True
        if job.proc is not None and IS_WINDOWS:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(job.proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
    return job_status(job_id)


def start(*, source: Path, plugin_path: str, params: dict[int, float], title: str,
          state_key: Optional[str] = None) -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = PluginJob(status="queued", plugin=Path(plugin_path).stem)
    asyncio.create_task(_run(job_id, source=source, plugin_path=plugin_path, params=params,
                             title=title, state_key=state_key))
    return job_id


async def _run(job_id: str, *, source: Path, plugin_path: str, params: dict[int, float], title: str,
               state_key: Optional[str] = None) -> None:
    job = _jobs[job_id]
    work_dir = db.model_dir("plugins") / job_id
    log_name = f"plugin_{job_id}"
    try:
        # One at a time: each run loads a plugin that may allocate freely and
        # assume it owns the machine's audio thread.
        async with _job_lock:
            if job.cancel_requested:
                job.status = "cancelled"
                return
            job.status = "running"
            if not host_available():
                raise RuntimeError(f"VST host not built at {VST_HOST_EXE}")
            work_dir.mkdir(parents=True, exist_ok=True)

            wav_in = await audio_buffers.prepare_source(source, work_dir / "in.wav")
            raw_in, raw_out = work_dir / "in.raw", work_dir / "out.raw"
            await audio_buffers.to_raw_f32(wav_in, raw_in)

            args = [
                str(VST_HOST_EXE), "process",
                "--plugin", plugin_path,
                "--in", str(raw_in), "--out", str(raw_out),
                "--rate", str(SAMPLE_RATE), "--channels", str(CHANNELS),
            ]
            # What the user set up in the plugin's window. Without it the
            # render would fall back to the plugin's defaults and quietly
            # discard everything they did in there.
            if state_key and has_state(state_key):
                args += ["--state", str(state_path(state_key))]
            for index, value in params.items():
                args += ["--param", f"{index}={value}"]

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / f"{log_name}.log", "w", encoding="utf-8", errors="replace") as log_file:
                proc = await asyncio.create_subprocess_exec(
                    *args, stdout=log_file, stderr=asyncio.subprocess.STDOUT,
                )
                job.proc = proc
                returncode = await proc.wait()
                job.proc = None

            if job.cancel_requested:
                job.status = "cancelled"
                return
            if returncode != 0 or not raw_out.exists():
                detail = (LOG_DIR / f"{log_name}.log").read_text(encoding="utf-8", errors="replace")[-400:]
                raise RuntimeError(f"plugin host exited with code {returncode}\n{detail}")

            out_dir = db.model_dir("plugins")
            processed = await audio_buffers.from_raw_f32(raw_out, out_dir / f"{job_id}.wav")

            job.track_id = db.insert_track(
                model="plugins",
                title=title or f"{job.plugin} print",
                lyrics="",
                seed=None,
                duration_ms=None,
                wall_ms=None,
                params={"plugin": plugin_path, "parameters": params, "state_key": state_key},
                audio_path=processed,
                abc_path=None,
            )
            job.status = "done"
    except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
        job.status = "failed"
        job.error = str(exc)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
