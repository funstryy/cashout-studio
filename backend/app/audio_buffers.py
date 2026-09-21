"""Audio in and out of numpy, and the ensemble maths that needs it.

Decoding and encoding both go through ffmpeg rather than a WAV library: the
separation models write whatever sample format they please, ffmpeg is already
a hard dependency, and piping raw f32 means resampling and channel layout are
handled on the way in instead of being another thing to get wrong.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import numpy as np

from .audio_io import AudioConversionError, get_ffmpeg_bin

SAMPLE_RATE = 44100
CHANNELS = 2

# UVR's vocabulary, because this is the same operation and people arrive here
# already knowing these names.
EnsembleAlgorithm = Literal["max_spec", "min_spec", "average"]

_FFT_SIZE = 4096
_HOP = 1024


async def _run_ffmpeg(args: list[str], stdin: bytes | None = None) -> bytes:
    ffmpeg_bin = get_ffmpeg_bin()
    if not ffmpeg_bin:
        raise AudioConversionError("ffmpeg not found on PATH or in FFMPEG_BIN_DIR")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y", *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=stdin)
    if proc.returncode != 0:
        raise AudioConversionError(err.decode("utf-8", "replace").strip()[:500])
    return out


async def decode(path: Path) -> np.ndarray:
    """(channels, samples) float32."""
    raw = await _run_ffmpeg([
        "-i", str(path),
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "pipe:1",
    ])
    samples = np.frombuffer(raw, dtype=np.float32)
    return samples.reshape(-1, CHANNELS).T.copy()


async def encode(audio: np.ndarray, dest: Path, fmt: str) -> Path:
    """Write (channels, samples) float32 as wav/flac/mp3."""
    dest = dest.with_suffix(f".{fmt}")
    interleaved = np.ascontiguousarray(audio.T, dtype=np.float32)
    codec = {"wav": ["-c:a", "pcm_s16le"], "flac": ["-c:a", "flac"], "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"]}[fmt]
    await _run_ffmpeg(
        [
            "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-i", "pipe:0", *codec, str(dest),
        ],
        stdin=interleaved.tobytes(),
    )
    return dest


async def prepare_source(path: Path, dest: Path, seconds: float | None = None) -> Path:
    """Decode any input into the WAV the native models insist on.

    audiocpp_cli rejects anything that isn't a WAV container outright - and
    tracks in the library are routinely mp3 or flac, since that's what the
    generators emit - so every job converts first rather than hoping. Passing
    `seconds` also trims, for previewing settings without processing a whole
    song; doing both in one ffmpeg pass avoids writing the file twice.
    """
    args = []
    if seconds is not None:
        args += ["-t", str(seconds)]
    args += [
        "-i", str(path),
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
        str(dest),
    ]
    await _run_ffmpeg(args)
    return dest


async def trim_file(source: Path, dest: Path, seconds: float, start: float = 0.0) -> Path:
    """Take `seconds` of a file from `start`, without re-encoding it.

    Stream-copies rather than transcoding: the point is to cut training clips
    down to a workable length, and re-encoding a folder of songs would both
    waste time and lose a generation of audio quality for no benefit.

    `start` matters for songs - cutting from 0:00 captures the intro, so a
    track included for its vocal gets described as an instrumental.
    """
    args = []
    if start > 0:
        args += ["-ss", str(start)]
    args += ["-t", str(seconds), "-i", str(source), "-c", "copy", str(dest)]
    await _run_ffmpeg(args)
    return dest


async def to_raw_f32(source: Path, dest: Path) -> Path:
    """Bare interleaved float32 - what the native plugin host reads."""
    raw = await _run_ffmpeg([
        "-i", str(source), "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "pipe:1",
    ])
    dest.write_bytes(raw)
    return dest


async def from_raw_f32(source: Path, dest: Path) -> Path:
    await _run_ffmpeg([
        "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        "-i", str(source), "-c:a", "pcm_s16le", str(dest),
    ])
    return dest


def _stft(channel: np.ndarray) -> np.ndarray:
    window = np.hanning(_FFT_SIZE).astype(np.float32)
    padded = np.pad(channel, (_FFT_SIZE // 2, _FFT_SIZE), mode="constant")
    frame_count = 1 + (len(padded) - _FFT_SIZE) // _HOP
    frames = np.lib.stride_tricks.as_strided(
        padded,
        shape=(frame_count, _FFT_SIZE),
        strides=(padded.strides[0] * _HOP, padded.strides[0]),
    )
    return np.fft.rfft(frames * window, axis=1)


def _istft(spectrum: np.ndarray, length: int) -> np.ndarray:
    window = np.hanning(_FFT_SIZE).astype(np.float32)
    frames = np.fft.irfft(spectrum, n=_FFT_SIZE, axis=1) * window
    out = np.zeros((len(frames) - 1) * _HOP + _FFT_SIZE, dtype=np.float32)
    weight = np.zeros_like(out)
    for index, frame in enumerate(frames):
        start = index * _HOP
        out[start:start + _FFT_SIZE] += frame
        weight[start:start + _FFT_SIZE] += window ** 2
    # Where the overlapped windows sum to ~0 there is no signal to recover,
    # and dividing would manufacture noise instead.
    np.divide(out, weight, out=out, where=weight > 1e-8)
    return out[_FFT_SIZE // 2:_FFT_SIZE // 2 + length]


def _align(sources: list[np.ndarray]) -> list[np.ndarray]:
    """Models pad and trim differently; compare only what they all produced."""
    length = min(source.shape[1] for source in sources)
    return [source[:, :length] for source in sources]


def ensemble(sources: list[np.ndarray], algorithm: EnsembleAlgorithm) -> np.ndarray:
    """Combine several models' takes on the same stem.

    "average" is the waveform mean - safe, and it dulls transients. The spec
    modes work per time-frequency bin instead: max keeps the loudest model's
    bin (fuller, keeps detail one model missed), min keeps the quietest
    (cleaner, removes anything the models disagree about, which is usually
    bleed). Each bin's phase travels with the magnitude that won it, rather
    than being taken from one fixed source and smearing the result.
    """
    if len(sources) == 1:
        return sources[0]
    aligned = _align(sources)
    if algorithm == "average":
        return np.mean(aligned, axis=0)

    picker = np.argmax if algorithm == "max_spec" else np.argmin
    channels, length = aligned[0].shape
    combined = np.zeros((channels, length), dtype=np.float32)
    for channel in range(channels):
        spectra = np.stack([_stft(source[channel]) for source in aligned])
        chosen = picker(np.abs(spectra), axis=0)
        picked = np.take_along_axis(spectra, chosen[None, ...], axis=0)[0]
        combined[channel] = _istft(picked, length)
    return combined


def invert(mixture: np.ndarray, stem: np.ndarray) -> np.ndarray:
    """Everything the stem isn't - how an instrumental is derived from vocals."""
    length = min(mixture.shape[1], stem.shape[1])
    return mixture[:, :length] - stem[:, :length]
