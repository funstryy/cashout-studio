#Requires -Version 5.1
<#
Clones the original ACE-Step-1.5 and audio.cpp (YuE2) repositories into
external/, applies Cashout Studio's small patches on top (see
external/patches/README.md), builds/prepares each engine for AMD hardware on
Windows 10, sets up a Demucs (stem separation) uv project in
external/Demucs, and writes backend/.env with an auto-detected
FFMPEG_BIN_DIR.

AMD/Windows 10 notes (see README for the full writeup):
  - YuE2 (audio.cpp) is built with its Vulkan compute backend
    (-Preset windows-vulkan-release). Vulkan works on any GPU vendor and
    needs no vendor SDK at runtime, so this is the reliable AMD GPU path.
  - ACE-Step-1.5 and Demucs are PyTorch-based, and PyTorch has no Vulkan
    path. On AMD it needs ROCm, whose Windows wheels want Windows 11 and a
    supported card, so these two run on CPU torch here. torch-directml was
    tried and dropped: ACE-Step has no DirectML device-selection path, so it
    never actually ran anything on the GPU, and directml's torch 2.4.1 pin
    broke ACE-Step's own dependencies (see Install-TorchCpu below).

Re-run any time - every step is idempotent (skips work that is already done).
#>
param(
    [switch]$SkipBuild,
    [switch]$SkipWeights
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$externalDir = Join-Path $root "external"
$patchesDir = Join-Path $externalDir "patches"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "== $msg ==" -ForegroundColor Cyan
}

function Assert-Command($name, $installHint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "[MISSING] '$name' is not on PATH. $installHint" -ForegroundColor Yellow
        return $false
    }
    return $true
}

function Find-FfmpegBinDir {
    $cmd = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        return Split-Path -Parent $cmd.Source
    }
    # Not on PATH yet - most likely setup_prereqs.ps1 just installed it via
    # winget in *this same terminal*; PATH only picks that up in a new one.
    # Look directly in winget's package cache instead of waiting for that.
    $searchRoots = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"),
        (Join-Path $env:ProgramFiles "WinGet\Packages")
    ) | Where-Object { Test-Path $_ }
    foreach ($searchRoot in $searchRoots) {
        $found = Get-ChildItem -Path $searchRoot -Filter "ffmpeg.exe" -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "Gyan\.FFmpeg" } |
            Select-Object -First 1
        if ($found) {
            return $found.DirectoryName
        }
    }
    return $null
}

function Initialize-Repo($dirName, $repoUrl, $refName, $patchFile) {
    $dir = Join-Path $externalDir $dirName
    if (-not (Test-Path $dir)) {
        Write-Host "Cloning $repoUrl ..."
        git clone $repoUrl $dir
    }

    Push-Location $dir
    try {
        $markerFile = Join-Path $dir ".remiqora-setup-done"
        $alreadyDone = Test-Path $markerFile
        if (-not $alreadyDone) {
            Write-Host "Checking out $refName ..."
            $prevPref = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            git checkout $refName 2>$null
            $checkedOut = ($LASTEXITCODE -eq 0)
            if (-not $checkedOut) {
                git fetch origin 2>$null
                git checkout $refName 2>$null
                $checkedOut = ($LASTEXITCODE -eq 0)
            }
            $ErrorActionPreference = $prevPref
            if (-not $checkedOut) {
                throw "Could not check out '$refName' in $dir - it may no longer exist upstream. See external/patches/README.md for how to bump the pinned commit."
            }

            if ($patchFile) {
                Write-Host "Applying $(Split-Path -Leaf $patchFile) ..."
                git apply --whitespace=nowarn $patchFile
            }
            New-Item -ItemType File -Path $markerFile -Force | Out-Null
        } else {
            Write-Host "Already checked out and patched, skipping."
        }
    } finally {
        Pop-Location
    }
    return $dir
}

# Installs the CPU build of torch that ACE-Step's own dependency set is
# written against. Run from inside the target project's directory so uv picks
# up its .venv.
#
# Why CPU and not torch-directml: directml pins torch 2.4.1, but ACE-Step
# targets 2.7.1 on Windows and leaves `diffusers` unpinned - so the resolver
# installs a current diffusers whose custom-op registration torch 2.4.1
# cannot parse, and the VAE fails to import with "Parameter q has unsupported
# type torch.Tensor" the first time you generate. Holding torch three minor
# versions behind the rest of the stack bought nothing anyway: ACE-Step has
# no DirectML device-selection path, so torch-directml never ran a single
# operation for it.
#
# Returns the pins to re-assert on every later "uv pip install" in this venv:
# otherwise uv is free to satisfy some other package's looser constraint by
# quietly swapping torch out from under the build that was just installed.
function Install-TorchCpu {
    $torchPins = @("torch==2.7.1", "torchvision==0.22.1", "torchaudio==2.7.1")
    Write-Host "Installing CPU torch 2.7.1 (matches ACE-Step's Windows target) ..."
    uv pip install @torchPins --index-url https://download.pytorch.org/whl/cpu
    if ($LASTEXITCODE -ne 0) {
        throw "torch install failed (exit $LASTEXITCODE)."
    }
    return $torchPins
}

Write-Step "ACE-Step-1.5 (AMD/Windows 10 setup)"
$aceDir = Initialize-Repo "ACE-Step-1.5" "https://github.com/ace-step/ACE-Step-1.5.git" "ca1e85f" (Join-Path $patchesDir "ace-step.patch")

if (Assert-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/") {
    Push-Location $aceDir
    try {
        if (-not (Test-Path ".venv")) {
            Write-Host "Creating .venv (Python 3.12) ..."
            uv venv --python 3.12
        }
        $torchPins = Install-TorchCpu
        # requirements-rocm.txt is upstream's non-CUDA dependency list (no
        # torch/torchvision/torchaudio pin of its own, and already excludes
        # CUDA-only extras like torchao/flash-attn/triton-windows) - reuse it
        # here as-is on top of the CPU torch installed above.
        # Re-asserting $torchPins in this same command is required, not just
        # redundant: without it, uv's resolver is free to swap torch for
        # whatever some other package here (accelerate/lightning/diffusers)
        # accepts, silently replacing the build everything else was resolved
        # against.
        Write-Host "Installing remaining dependencies (requirements-rocm.txt) ..."
        uv pip install -r requirements-rocm.txt @torchPins

        # The backend launches this via "uv run --no-sync acestep-api", which
        # execs the "acestep-api" console-script entry point declared in
        # ACE-Step-1.5's own pyproject.toml ([project.scripts]). That entry
        # point is only created when the project itself gets installed into
        # .venv (editable, so it stays in sync with the checked-out source) -
        # the dependency-only installs above never do that on their own.
        # --no-deps: dependencies are already pinned and installed above;
        # this step must not re-resolve them (that's what pulled in the cu128
        # torch build the first time - see Install-TorchCpu's comment).
        Write-Host "Installing ACE-Step-1.5 itself (editable, for the acestep-api entry point) ..."
        uv pip install -e . --no-deps
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Skipped ACE-Step dependency install - install uv and re-run this script." -ForegroundColor Yellow
}

Write-Step "audio.cpp (YuE2 - Vulkan build)"
# No patch needed here - upstream's dev branch natively exposes what our own
# patch used to add. dev is a moving, occasionally force-pushed branch
# upstream; if this exact commit 404s, bump it (see external/patches/README.md).
$audioCppDir = Initialize-Repo "audio.cpp" "https://github.com/0xShug0/audio.cpp.git" "39f9013" $null

if ($SkipBuild) {
    Write-Host "Skipping build (-SkipBuild passed)."
} else {
    $haveCmake = Assert-Command "cmake" "Install CMake from https://cmake.org/download/"
    if ($haveCmake) {
        Push-Location $audioCppDir
        try {
            $models = "yue2,sheetsage2,muscriptor,chatterbox,seed_vc,bs_roformer,mel_band_roformer,htdemucs"
            Write-Host "Building audiocpp_server (Vulkan release, $models) ..."
            Write-Host "Needs the Vulkan SDK (setup_prereqs.ps1) and VS Build Tools C++ workload on PATH;" -ForegroundColor DarkGray
            Write-Host "if the build fails here, open a 'Developer PowerShell for VS' and re-run this script." -ForegroundColor DarkGray
            # chatterbox + seed_vc are the voice models: speaking in an
            # imported voice, and converting existing audio (including
            # singing) to it. See backend/app/voices.py.
            # bs_roformer/mel_band_roformer/htdemucs are the separation
            # models behind the UVR-style separation lab - several on
            # purpose, since ensembling them is the point. See
            # backend/app/separation.py.
            foreach ($target in "audiocpp_server", "audiocpp_cli") {
                # audiocpp_cli as well as the server: voice conversion has no
                # HTTP route in the server, so Cashout Studio runs it as a CLI job.
                Write-Host "  target: $target"
                powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build_windows.ps1" `
                    -Preset windows-vulkan-release `
                    -ModelSet custom -Models $models `
                    -NativeModelManager `
                    -DeploymentBuild `
                    -Target $target
                if ($LASTEXITCODE -ne 0) {
                    throw "audio.cpp Vulkan build of $target failed (exit $LASTEXITCODE) - see external/audio.cpp/README.md."
                }
            }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "Skipped native build - install the missing tools above, then re-run:" -ForegroundColor Yellow
        Write-Host "  .\setup_models.ps1 " -NoNewline -ForegroundColor Yellow
        Write-Host "(or run the build manually per external/audio.cpp/README.md)" -ForegroundColor Yellow
    }
}

if ($SkipWeights) {
    Write-Step "YuE2/SheetSage2/MuScriptor weights"
    Write-Host "Skipping weight downloads (-SkipWeights passed)."
} elseif (Assert-Command "python" "Install Python 3 and put it on PATH.") {
    Write-Step "YuE2/SheetSage2/MuScriptor + voice model weights (~12 GB total)"
    Push-Location $audioCppDir
    try {
        foreach ($pkg in "yue2_main_q8_0", "yue2_main_q4_0", "yue2_vae_f16", "sheetsage2_orig", "muscriptor_small_f32",
                         "chatterbox_q8_0", "seed_vc_mlx_q8_0",
                         "bs_roformer_q8_0", "mel_band_roformer_q8_0", "htdemucs_q8_0") {
            Write-Host "Installing $pkg ..."
            python tools/model_manager_v2.py install $pkg
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Skipped weight downloads - install Python and re-run this script." -ForegroundColor Yellow
}
# ACE-Step's own checkpoints (acestep-v15-sft, LM, VAE, ...) are not fetched
# here - acestep-api downloads them itself via HuggingFace/ModelScope on its
# first request, the same way its Gradio UI does.

Write-Step "Demucs (stem separation, AMD/Windows 10 setup)"
$demucsDir = Join-Path $externalDir "Demucs"
if (-not (Test-Path $demucsDir)) {
    New-Item -ItemType Directory -Path $demucsDir -Force | Out-Null
}
if (Assert-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/") {
    Push-Location $demucsDir
    try {
        if (-not (Test-Path ".venv")) {
            Write-Host "Creating .venv (Python 3.12) ..."
            uv venv --python 3.12
        }
        $torchPins = Install-TorchCpu
        # numpy explicitly: demucs imports it directly (see
        # demucs/transformer.py) but its own package metadata doesn't
        # declare it as a dependency, so it's otherwise missing at import time.
        # $torchPins re-asserted here for the same reason as ACE-Step above -
        # otherwise demucs's own torch constraint can pull the resolver onto
        # a different build than the one just installed.
        Write-Host "Installing demucs ..."
        uv pip install demucs numpy @torchPins
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Skipped Demucs dependency install - install uv and re-run this script." -ForegroundColor Yellow
}

Write-Step "backend/.env"
$envFile = Join-Path $root "backend\.env"
$ffmpegBinDir = Find-FfmpegBinDir
if ($ffmpegBinDir) {
    Write-Host "Found ffmpeg at $ffmpegBinDir"
} else {
    Write-Host "Could not find ffmpeg (install it via setup_prereqs.ps1) - FFMPEG_BIN_DIR will need setting by hand." -ForegroundColor Yellow
}
if (-not (Test-Path $envFile)) {
    $envLines = @(
        "ACE_STEP_DIR=$aceDir",
        "YUE2_DIR=$audioCppDir",
        "DEMUCS_DIR=$demucsDir",
        "YUE2_BUILD_DIR=windows-vulkan-release"
    )
    if ($ffmpegBinDir) {
        $envLines += "FFMPEG_BIN_DIR=$ffmpegBinDir"
    }
    $envLines | Set-Content $envFile
    Write-Host "Wrote backend/.env pointing at the cloned repos."
    if (-not $ffmpegBinDir) {
        Write-Host "Still add FFMPEG_BIN_DIR to backend/.env by hand (see backend/.env.example)." -ForegroundColor Yellow
    }
} else {
    Write-Host "backend/.env already exists - not overwriting. Cloned repo paths:"
    Write-Host "  ACE_STEP_DIR=$aceDir"
    Write-Host "  YUE2_DIR=$audioCppDir"
    Write-Host "  DEMUCS_DIR=$demucsDir"
    Write-Host "  YUE2_BUILD_DIR=windows-vulkan-release"
    if ($ffmpegBinDir) {
        Write-Host "  FFMPEG_BIN_DIR=$ffmpegBinDir (detected - edit backend/.env if it doesn't already match)"
    }
}

Write-Step "Done"
Write-Host "Remaining manual steps (see README.md):"
if (-not $ffmpegBinDir) {
    Write-Host "  - Install ffmpeg (setup_prereqs.ps1) and add FFMPEG_BIN_DIR to backend/.env."
}
Write-Host "  - ACE-Step's own checkpoints download automatically on its first request."
Write-Host "  - Then run dev.bat or prod_run.bat."
