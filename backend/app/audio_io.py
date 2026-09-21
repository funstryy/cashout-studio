"""Locating ffmpeg and transcoding uploads to WAV.

Both the YuE2 upload proxy and the voice library need the same thing: the
native engine's WAV reader only accepts an actual WAV container (PCM/float/
A-law/mu-law), so anything a browser hands us (mp3, m4a, ogg, webm from a mic
recording) has to be converted first.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .config import FFMPEG_BIN_DIR

IS_WINDOWS = sys.platform == "win32"


class AudioConversionError(RuntimeError):
    """ffmpeg is missing, or it refused the input."""


def get_ffmpeg_bin() -> str | None:
    candidate = FFMPEG_BIN_DIR / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
    if candidate.is_file():
        return str(candidate)
    env_bin = os.environ.get("FFMPEG_BIN")
    if env_bin:
        found = shutil.which(env_bin)
        if found:
            return found
        if Path(env_bin).is_file():
            return env_bin
    return shutil.which("ffmpeg")


async def to_wav(data: bytes, *, sample_rate: int = 44100, channels: int = 1) -> bytes:
    ffmpeg_bin = get_ffmpeg_bin()
    if not ffmpeg_bin:
        raise AudioConversionError("ffmpeg not found on PATH or in FFMPEG_BIN_DIR")
    # A regular (seekable) output file, not a pipe: ffmpeg can't know the
    # final byte count up front when muxing WAV, and on a pipe it can't seek
    # back afterwards to patch the RIFF/data chunk sizes.
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
            "-i", "pipe:0", "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", out_path,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate(input=data)
        if proc.returncode != 0:
            raise AudioConversionError(
                f"Audio conversion to WAV failed: {stderr.decode('utf-8', 'replace').strip()[:500]}"
            )
        return Path(out_path).read_bytes()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
