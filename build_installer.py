#!/usr/bin/env python3
"""Builds CashoutStudio-Setup-<version>.exe, the wizard end users run.

Stages everything the installed app needs into build/installer_payload/ and
hands that to Inno Setup's compiler:

    app/      the frozen app from build_dist.py (exe, _internal, frontend_dist)
    engines/  audiocpp_server.exe, audiocpp_cli.exe and model_specs/
    ffmpeg/   ffmpeg.exe and ffprobe.exe

What is deliberately NOT in here: the ~13 GB of model weights. An installer
that large is impractical to build, host and download, so the wizard instead
offers to point at weights you already have, and the app can fetch them
afterwards. Everything else is bundled, so a fresh machine needs no git, no
compiler, no Python and no uv to run the app.

Usage:
    python build_installer.py                  # build the app first, then the installer
    python build_installer.py --skip-app       # reuse the existing CashoutStudio/ build
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "CashoutStudio"
PAYLOAD_DIR = ROOT / "build" / "installer_payload"
OUTPUT_DIR = ROOT / "build" / "installer"
INSTALLER_SCRIPT = ROOT / "installer" / "cashout-studio.iss"
LICENSE_FILE = ROOT / "LICENSE"
VERSION = "0.1.0"

YUE2_BUILD_DIR = "windows-vulkan-release"
AUDIOCPP_DIR = ROOT / "external" / "audio.cpp"
# Only what the app actually launches - the source checkout also holds build
# trees and 13 GB of weights, none of which belong in an installer.
ENGINE_BINARIES = ("audiocpp_server.exe", "audiocpp_cli.exe")
FFMPEG_BINARIES = ("ffmpeg.exe", "ffprobe.exe")
# Written by build_dist.py but owned by the running app afterwards.
APP_RUNTIME_DIRS = ("data", "logs")


def find_iscc() -> Path:
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    raise SystemExit(
        "Inno Setup's compiler (ISCC.exe) was not found.\n"
        "Install it with:  winget install --id JRSoftware.InnoSetup"
    )


def find_ffmpeg_bin_dir() -> Path | None:
    """The folder holding ffmpeg.exe - from .env if it's set there, otherwise
    wherever it is on PATH."""
    env_file = ROOT / "backend" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "FFMPEG_BIN_DIR" and Path(value.strip()).is_dir():
                return Path(value.strip())
    found = shutil.which("ffmpeg")
    return Path(found).parent if found else None


def stage_app() -> None:
    print(f"\n== Staging app from {DIST_DIR} ==")
    if not (DIST_DIR / "CashoutStudio.exe").exists():
        raise SystemExit(f"{DIST_DIR / 'CashoutStudio.exe'} not found - run build_dist.py first.")
    dest = PAYLOAD_DIR / "app"
    dest.mkdir(parents=True, exist_ok=True)
    for item in DIST_DIR.iterdir():
        # The install must start with an empty library and a fresh .env, not
        # with whatever this build machine happens to have accumulated - and
        # .env in particular holds absolute paths that mean nothing elsewhere.
        if item.name in APP_RUNTIME_DIRS or item.name == ".env":
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
        print(f"  {item.name}")


def stage_engines() -> None:
    print("\n== Staging engine binaries and model specs ==")
    bin_src = AUDIOCPP_DIR / "build" / YUE2_BUILD_DIR / "bin"
    bin_dest = PAYLOAD_DIR / "engines" / "audio.cpp" / "build" / YUE2_BUILD_DIR / "bin"
    bin_dest.mkdir(parents=True, exist_ok=True)
    for name in ENGINE_BINARIES:
        source = bin_src / name
        if not source.exists():
            raise SystemExit(f"{source} not found - build audio.cpp first (setup_models.ps1).")
        shutil.copy2(source, bin_dest / name)
        print(f"  {name}")

    # audiocpp_server resolves model_specs/ relative to its working directory,
    # which is the engine folder - without these it cannot load anything.
    specs_src = AUDIOCPP_DIR / "model_specs"
    specs_dest = PAYLOAD_DIR / "engines" / "audio.cpp" / "model_specs"
    if not specs_src.is_dir():
        raise SystemExit(f"{specs_src} not found - clone audio.cpp first (setup_models.ps1).")
    shutil.copytree(specs_src, specs_dest, dirs_exist_ok=True)
    print(f"  model_specs/ ({len(list(specs_dest.glob('*.json')))} specs)")

    # So a fresh install has somewhere obvious for the weights to land.
    (PAYLOAD_DIR / "engines" / "audio.cpp" / "models").mkdir(parents=True, exist_ok=True)


def stage_ffmpeg() -> None:
    print("\n== Staging ffmpeg ==")
    source_dir = find_ffmpeg_bin_dir()
    if not source_dir:
        raise SystemExit(
            "ffmpeg not found. Install it (setup_prereqs.ps1) or set FFMPEG_BIN_DIR in backend/.env."
        )
    dest = PAYLOAD_DIR / "ffmpeg" / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    for name in FFMPEG_BINARIES:
        source = source_dir / name
        if not source.exists():
            raise SystemExit(f"{source} not found.")
        shutil.copy2(source, dest / name)
        print(f"  {name} ({source.stat().st_size // (1024 * 1024)} MB)")


def compile_installer() -> Path:
    iscc = find_iscc()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n== Compiling with {iscc} (this compresses ~500 MB, give it a few minutes) ==")
    subprocess.run(
        [
            str(iscc),
            f"/DPayloadDir={PAYLOAD_DIR}",
            f"/DOutputDir={OUTPUT_DIR}",
            f"/DAppVersion={VERSION}",
            f"/DLicenseFile={LICENSE_FILE}",
            str(INSTALLER_SCRIPT),
        ],
        check=True,
    )
    installer = OUTPUT_DIR / f"CashoutStudio-Setup-{VERSION}.exe"
    if not installer.exists():
        raise SystemExit(f"Inno Setup finished but {installer} is missing.")
    return installer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-app", action="store_true", help="reuse the existing CashoutStudio/ build")
    args = parser.parse_args()

    if not args.skip_app:
        subprocess.run([sys.executable, str(ROOT / "build_dist.py")], check=True)

    if PAYLOAD_DIR.exists():
        shutil.rmtree(PAYLOAD_DIR)
    stage_app()
    stage_engines()
    stage_ffmpeg()
    installer = compile_installer()

    size_mb = installer.stat().st_size // (1024 * 1024)
    print(f"\nDone. Installer: {installer} ({size_mb} MB)")
    print("It installs per-user into %LOCALAPPDATA%\\Programs\\Cashout Studio - no admin needed.")
    print("Model weights are not bundled; the wizard offers to reuse an existing engine folder.")


if __name__ == "__main__":
    main()
