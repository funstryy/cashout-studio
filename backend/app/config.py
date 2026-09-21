"""Static configuration: paths to the two model repos, ports, launch commands.

All values here were verified against the real launch scripts / CLI argparse
definitions in each project (see the plan doc) rather than guessed, since a
wrong flag here means a multi-minute GPU model load fails at the very end.

Machine-specific filesystem paths are read from a `.env` file next to this
package (backend/.env, see backend/.env.example) so redeploying on another
machine only means editing that one file, not this source file.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# In a normal checkout this file lives at <repo>/backend/app/config.py, so
# two parents up is <repo>/backend. Under a PyInstaller --onedir build,
# __file__ instead resolves inside the frozen bundle's internal data dir, so
# machine-specific files (.env, logs/, data/, frontend/dist) are anchored to
# the directory holding the .exe instead - see build_dist.py.
if getattr(sys, "frozen", False):
    _APP_ROOT = Path(sys.executable).resolve().parent
else:
    _APP_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(_APP_ROOT / ".env")


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


@dataclass(frozen=True)
class ProcessSpec:
    """One OS process to launch as part of bringing a model online."""

    name: str
    cwd: Path
    cmd: list[str]
    # Extra directories prepended to PATH for this process only.
    extra_path_dirs: list[Path] = field(default_factory=list)
    # Extra environment variables (on top of the inherited environment).
    env: dict[str, str] = field(default_factory=dict)
    # URL polled to decide the process is up and ready to receive traffic.
    health_url: str = ""
    # Seconds to wait for health_url to respond before declaring failure.
    startup_timeout: float = 300.0
    # Seconds to wait for graceful exit before force-killing the process tree.
    shutdown_timeout: float = 20.0


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    label: str
    # Processes started in order; each subsequent one waits for the previous
    # process's health_url before it is launched.
    processes: list[ProcessSpec]
    # URL path segment: requests to "/api/{proxy_prefix}/*" are forwarded to proxy_target.
    proxy_prefix: str
    # Base URL the reverse proxy forwards "/api/{proxy_prefix}/*" requests to.
    proxy_target: str
    # Health URL used by the orchestrator to represent "is this model usable".
    health_url: str


# Default to external/<repo> next to the app root (repo root in a normal
# checkout, the folder holding the .exe in a PyInstaller build) so a
# portable dist folder works with no .env at all as long as external/ sits
# alongside it - see build_dist.py. .env can still override these for a
# setup that keeps external/ somewhere else (e.g. a bigger drive).
_EXTERNAL_DIR = _APP_ROOT.parent / "external"
ACE_STEP_DIR = _env_path("ACE_STEP_DIR", str(_EXTERNAL_DIR / "ACE-Step-1.5"))
YUE2_DIR = _env_path("YUE2_DIR", str(_EXTERNAL_DIR / "audio.cpp"))
# Separate uv-managed venv for Demucs (stem separation) - not a "model" in
# MODELS below since it's a one-shot CLI job, not a persistent HTTP server.
DEMUCS_DIR = _env_path("DEMUCS_DIR", str(_EXTERNAL_DIR / "Demucs"))
DEMUCS_EXE = _env_path("DEMUCS_EXE", str(DEMUCS_DIR / ".venv" / "Scripts" / "demucs.exe"))
# torch device for a separation. CPU by default: the AMD/Windows torch build
# has no GPU path anyway, and keeping stems off the GPU means they can run
# while a model is loaded. A CUDA machine can set "cuda" here.
DEMUCS_DEVICE = os.getenv("DEMUCS_DEVICE", "cpu")
# Threads a separation may use. A quarter of the logical CPUs is roughly
# half the physical cores on an SMT machine, which is the point: torch
# already defaults to one thread per physical core, so anything less
# aggressive than this wouldn't actually leave room for the active model
# (or the UI) while stems run - see stems.py.
DEMUCS_THREADS = int(os.getenv("DEMUCS_THREADS", str(max(1, (os.cpu_count() or 4) // 4))))

# MuScriptor (audio -> MIDI) is loaded into YuE2's own audiocpp_server rather
# than being launched separately, so it gets no MODELS entry - only the spec
# that server needs to resolve the weights.
MUSCRIPTOR_MODEL_PATH = _env_path(
    "MUSCRIPTOR_MODEL_PATH",
    str(YUE2_DIR / "models" / "MuScriptor-Small-GGUF" / "muscriptor-small-f32.gguf"),
)
MUSCRIPTOR_MODEL_ID = "muscriptor"
MUSCRIPTOR_FAMILY = "muscriptor"
MUSCRIPTOR_TASK = "midi"

YUE2_MODEL_PATH = _env_path(
    "YUE2_MODEL_PATH",
    str(YUE2_DIR / "models" / "Yue2-3B-GGUF"),
)
SHEETSAGE_MODEL_PATH = _env_path(
    "SHEETSAGE_MODEL_PATH",
    str(YUE2_DIR / "models" / "SheetSage2-GGUF" / "sheetsage2-orig.gguf"),
)


# Stable Audio 3 Medium: text-to-music, init-audio and inpainting, in 8
# diffusion steps rather than ACE-Step's sixty. Instrumental-focused, and the
# only edit-capable music model here that runs on the GPU through Vulkan -
# which is what makes generative fill usable on an AMD card.
STABLE_AUDIO_MODEL_PATH = _env_path(
    "STABLE_AUDIO_MODEL_PATH",
    # The package installs to this directory name, capitalised, with the
    # single .gguf inside it - not the lowercase path the CLI examples use.
    str(YUE2_DIR / "models" / "Stable-Audio-3-Medium-GGUF" / "stable-audio-3-medium-q8_0.gguf"),
)


def yue2_specs() -> dict[str, dict[str, str]]:
    return {
        "yue2": {
            "id": "yue2",
            "family": "yue2",
            "path": str(YUE2_MODEL_PATH).replace("\\", "/"),
            "task": "gen",
            "mode": "offline",
        },
        "sheetsage2": {
            "id": "sheetsage2",
            "family": "sheetsage2",
            "path": str(SHEETSAGE_MODEL_PATH).replace("\\", "/"),
            "task": "midi",
            "mode": "offline",
        },
        "muscriptor": {
            "id": MUSCRIPTOR_MODEL_ID,
            "family": MUSCRIPTOR_FAMILY,
            "path": str(MUSCRIPTOR_MODEL_PATH).replace("\\", "/"),
            "task": MUSCRIPTOR_TASK,
            "mode": "offline",
        },
        # Stable Audio 3 runs inside the same audiocpp_server as everything
        # above - the server hosts a family per model, not a process per
        # model. It is listed here rather than given an engine entry of its
        # own for exactly that reason.
        "stable_audio": {
            "id": "stable_audio",
            "family": "stable_audio",
            "path": str(STABLE_AUDIO_MODEL_PATH).replace("\\", "/"),
            "task": "gen",
            "mode": "offline",
        },
    }

FFMPEG_BIN_DIR = _env_path("FFMPEG_BIN_DIR", r"E:\AI\ACE\tools\ffmpeg-shared\ffmpeg-master-latest-win64-gpl-shared\bin")
# audio.cpp is built with the Vulkan backend (-Preset windows-vulkan-release,
# see setup_models.ps1) - Vulkan needs no vendor SDK at runtime, just the
# system Vulkan loader (vulkan-1.dll) that ships with any GPU driver, so
# there's no CUDA_BIN_DIR/ROCM_PATH equivalent to configure here.
# Which build\<preset> subdir under YUE2_DIR\build to launch (matches
# build_windows.ps1's -Preset, default "windows-vulkan-release").
YUE2_BUILD_DIR = os.getenv("YUE2_BUILD_DIR", "windows-vulkan-release")
AUDIOCPP_SERVER_EXE = _env_path(
    "AUDIOCPP_SERVER_EXE",
    str(YUE2_DIR / "build" / YUE2_BUILD_DIR / "bin" / "audiocpp_server.exe"),
)
# audiocpp_server's compute backend and device index (see --list-devices).
# "vulkan" is the vendor-neutral default this project builds; an NVIDIA
# machine that rebuilt with -Preset windows-cuda-release can set "cuda".
YUE2_BACKEND = os.getenv("YUE2_BACKEND", "vulkan")
YUE2_DEVICE = os.getenv("YUE2_DEVICE", "0")
# audiocpp_server hosts three models (yue2, sheetsage2, muscriptor) in one
# process and by default keeps every model it has loaded resident. YuE2-3B
# q8_0 alone is ~3.5 GB of VRAM, so on a typical 6-8 GB card a generate
# followed by an audio->MIDI conversion would try to hold two large models at
# once and fall back to host memory (or fail the load outright). Capping
# residency at one model makes the second load evict the first instead.
YUE2_MAX_LOADED_MODELS = os.getenv("YUE2_MAX_LOADED_MODELS", "1")
# Unload after this long with no inference so an idle-but-running YuE2 stops
# holding VRAM (0 disables). The next request reloads lazily.
YUE2_IDLE_UNLOAD_MS = os.getenv("YUE2_IDLE_UNLOAD_MS", "300000")

# The out-of-process VST3 host (host/CMakeLists.txt). Native code cannot run
# in the WebView, so plugin effects are printed through this helper rather
# than monitored live - see plugin_host.py.
VST_HOST_EXE = _env_path(
    "VST_HOST_EXE",
    str(_APP_ROOT.parent / "host" / "build" / "Release" / "cashout_vst_host.exe"),
)

# Whether the two engines may be up at the same time.
#
# They were made mutually exclusive when both ran on the GPU and 6-8 GB could
# not hold two models. That no longer describes this setup: YuE2 runs on the
# GPU through Vulkan while ACE-Step runs on the CPU, so they contend for
# nothing and serializing them just leaves half the machine idle. Set this to
# 0 on a box where both engines target the same GPU (an NVIDIA machine with
# ACESTEP_DEVICE=cuda), where the old exclusion is still what you want.
CONCURRENT_ENGINES = os.getenv("CONCURRENT_ENGINES", "1").strip().lower() not in ("0", "false", "no")

# ACE-Step's own console-script entry point, created in its venv by
# "uv pip install -e ." (see setup_models.ps1). Launched directly rather
# than through "uv run" so that starting a model needs neither uv on PATH
# nor a dependency re-resolve - uv re-resolving here is what used to
# silently replace the installed torch build on every launch.
# Which backend ACE-Step runs its language model through. See the launch
# environment below for why the default is the slow-but-portable one.
ACESTEP_LM_BACKEND = os.getenv("ACESTEP_LM_BACKEND", "pt")

ACESTEP_API_EXE = _env_path(
    "ACESTEP_API_EXE",
    str(ACE_STEP_DIR / ".venv" / "Scripts" / "acestep-api.exe"),
)

# audiocpp_cli runs the one-shot jobs the native server has no HTTP route for
# (voice conversion), the same way Demucs is driven as a CLI job.
AUDIOCPP_CLI_EXE = _env_path(
    "AUDIOCPP_CLI_EXE",
    str(YUE2_DIR / "build" / YUE2_BUILD_DIR / "bin" / "audiocpp_cli.exe"),
)
# Backend for those CLI jobs. They run in their own process, so unlike the
# models inside audiocpp_server they don't share its residency cap - set this
# to "cpu" if a conversion alongside an active engine is too much for the card.
VOICE_BACKEND = os.getenv("VOICE_BACKEND", YUE2_BACKEND)

# Shared track storage: one SQLite DB + files split into a subfolder per model.
DATA_DIR = _APP_ROOT / "data"

# Imported reference voices, one <name>.wav each plus a "prompt_text" mapping
# file of "<name>|<transcript>" lines. Handed to audiocpp_server as its
# --voice-dir, which is what makes them selectable as cloning references in
# /v1/audio/speech requests.
VOICES_DIR = _env_path("VOICES_DIR", str(DATA_DIR / "voices"))

# Voice models loaded into audiocpp_server / audiocpp_cli on demand, in the
# same shape as yue2_specs(). Chatterbox turns text into speech in an
# imported voice; Seed-VC converts existing audio (including singing) to it.
CHATTERBOX_MODEL_PATH = _env_path(
    "CHATTERBOX_MODEL_PATH",
    str(YUE2_DIR / "models" / "Chatterbox-GGUF" / "chatterbox-q8_0.gguf"),
)
SEED_VC_MODEL_PATH = _env_path(
    "SEED_VC_MODEL_PATH",
    str(YUE2_DIR / "models" / "SeedVC-MLX-GGUF" / "seed-vc-mlx-q8_0.gguf"),
)


_MODELS_DIR = YUE2_DIR / "models"

# Source-separation models, all driven through audiocpp_cli's `sep` task.
# Several are offered rather than one because they disagree in useful ways -
# running two and combining their output is exactly what an ensemble is for.
SEPARATION_MODELS: dict[str, dict] = {
    "bs_roformer": {
        "id": "bs_roformer",
        "label": "BS-RoFormer ep368",
        "family": "bs_roformer",
        "path": str(_MODELS_DIR / "BS-RoFormer-ep368-GGUF" / "bs-roformer-ep368-q8_0.gguf"),
        "stems": ["vocals", "instrumental"],
    },
    "mel_band_roformer": {
        "id": "mel_band_roformer",
        "label": "Mel-Band RoFormer",
        "family": "mel_band_roformer",
        "path": str(_MODELS_DIR / "Mel-Band-RoFormer-GGUF" / "mel-band-roformer-q8_0.gguf"),
        "stems": ["vocals", "instrumental"],
    },
    "htdemucs": {
        "id": "htdemucs",
        "label": "HTDemucs v4",
        "family": "htdemucs",
        "path": str(_MODELS_DIR / "HTDemucs-GGUF" / "htdemucs-q8_0.gguf"),
        "stems": ["vocals", "drums", "bass", "other"],
    },
}


def voice_specs() -> dict[str, dict[str, str]]:
    return {
        "chatterbox": {
            "id": "chatterbox",
            "family": "chatterbox",
            "path": str(CHATTERBOX_MODEL_PATH).replace("\\", "/"),
            "task": "clon",
            "mode": "offline",
        },
        "seed_vc": {
            "id": "seed_vc",
            "family": "seed_vc",
            "path": str(SEED_VC_MODEL_PATH).replace("\\", "/"),
            "task": "vc",
            "mode": "offline",
        },
    }


ACE_STEP_API_PORT = 8001
YUE2_SERVER_PORT = 8080

MODELS: dict[str, ModelDefinition] = {
    "ace_step": ModelDefinition(
        id="ace_step",
        label="ACE-Step 1.5",
        proxy_prefix="ace",
        proxy_target=f"http://127.0.0.1:{ACE_STEP_API_PORT}",
        health_url=f"http://127.0.0.1:{ACE_STEP_API_PORT}/health",
        processes=[
            ProcessSpec(
                name="ace_step_api",
                cwd=ACE_STEP_DIR,
                cmd=[
                    str(ACESTEP_API_EXE),
                    "--host", "127.0.0.1",
                    "--port", str(ACE_STEP_API_PORT),
                    "--lm-model-path", "acestep-5Hz-lm-1.7B",
                ],
                extra_path_dirs=[FFMPEG_BIN_DIR],
                env={
                    "PYTHONUTF8": "1",
                    # nano-vllm's LM backend needs flash-attn, which is
                    # CUDA-only and not installed on an AMD/CPU setup (see
                    # setup_models.ps1); "pt" runs the LM through plain
                    # PyTorch instead. On an NVIDIA machine whose environment
                    # does have flash-attn, "nanovllm" is considerably
                    # faster - which is why this is a setting and not a
                    # constant.
                    "ACESTEP_LM_BACKEND": ACESTEP_LM_BACKEND,
                },
                health_url=f"http://127.0.0.1:{ACE_STEP_API_PORT}/health",
                # Model + LM weights loading onto the GPU can genuinely take
                # a few minutes on first load / cold cache.
                startup_timeout=600.0,
            ),
        ],
    ),
    "yue2": ModelDefinition(
        id="yue2",
        label="YuE2 · Audio engine",
        proxy_prefix="yue2",
        # Proxied straight to the native inference server - we no longer run
        # YuE2's own web-ui/server.py. The one thing it did beyond plain
        # proxying (transcoding non-WAV uploads to WAV before forwarding to
        # /v1/ui/upload) is reimplemented in api/routes_yue2_upload.py.
        proxy_target=f"http://127.0.0.1:{YUE2_SERVER_PORT}",
        health_url=f"http://127.0.0.1:{YUE2_SERVER_PORT}/health",
        processes=[
            ProcessSpec(
                name="yue2_server",
                # cwd is the audio.cpp checkout so that model_specs/ (looked
                # up relative to the working directory) resolves. The GGUF
                # weights themselves are passed as absolute paths on load -
                # see yue2_specs() - so they don't depend on cwd.
                cwd=YUE2_DIR,
                cmd=[
                    str(AUDIOCPP_SERVER_EXE),
                    # --ui-management requires the exe to have been built with
                    # -NativeModelManager (build_windows.ps1); without it the
                    # server refuses to start with this flag at all.
                    "--ui", "--ui-management",
                    "--backend", YUE2_BACKEND,
                    "--device", YUE2_DEVICE,
                    # Imported voices become selectable cloning references in
                    # /v1/audio/speech requests - see voices.py.
                    "--voice-dir", str(VOICES_DIR),
                    "--max-loaded-models", YUE2_MAX_LOADED_MODELS,
                    "--idle-unload-ms", YUE2_IDLE_UNLOAD_MS,
                ],
                health_url=f"http://127.0.0.1:{YUE2_SERVER_PORT}/health",
                startup_timeout=300.0,
            ),
        ],
    ),
}

LOG_DIR = _APP_ROOT / "logs"
LOG_TAIL_LINES = 40

# Directory containing the built frontend (frontend/dist in a normal checkout,
# <dist>/frontend_dist next to the .exe in a PyInstaller build - see
# build_dist.py). Only used when it exists; in dev the Vite dev server is
# used instead and this is ignored.
if getattr(sys, "frozen", False):
    FRONTEND_DIST_DIR = _APP_ROOT / "frontend_dist"
else:
    FRONTEND_DIST_DIR = _APP_ROOT.parent / "frontend" / "dist"
