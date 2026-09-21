#!/usr/bin/env python3
"""Packages Cashout Studio into a portable, folder-based Windows app.

Produces, at the repo root (a sibling of external/, which this deliberately
never touches or copies - the ~10 GB of model weights and the two model
venvs stay exactly where setup_models.ps1 put them):

    CashoutStudio/
        CashoutStudio.exe          <- the app: backend + native window, no console
        frontend_dist/        <- built Vue SPA (frontend/dist)
        .env                  <- copied from backend/.env on the first build only

Rebuilding replaces only those. Everything the app itself writes into that
folder once it runs - data/ (track database, generated audio, imported
voices), logs/, and an .env you have since edited - is left untouched.

Why --onedir and not --onefile: PyInstaller's --onefile self-extracts into a
temp directory on every launch, which is pointless overhead for an app whose
actual bulk (10 GB of model weights in external/) already lives outside the
bundle entirely. --onedir starts instantly and is trivial to inspect.

Usage:
    python build_dist.py                  # build frontend + app
    python build_dist.py --skip-frontend  # reuse an already-built frontend/dist
    python build_dist.py --console        # keep a console window (for debugging)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DIST_NAME = "CashoutStudio"
DIST_DIR = ROOT / DIST_NAME
PYINSTALLER_WORKPATH = ROOT / "build" / "pyinstaller"
# PyInstaller is pointed at a staging folder rather than straight at
# DIST_DIR, because --noconfirm deletes its output folder wholesale - and
# DIST_DIR is where the running app keeps data/ and logs/. Freezing into
# staging and then moving only the build outputs across keeps that data
# safe no matter what PyInstaller decides to clear.
PYINSTALLER_DISTPATH = ROOT / "build" / "dist"


def run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}  (in {cwd})")
    subprocess.run(cmd, cwd=cwd, check=True)


def backend_python() -> str:
    """Prefer backend/.venv's interpreter (same one dev.bat/prod_run.bat use)."""
    venv_python = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    print("[warn] backend/.venv not found - falling back to the current interpreter.")
    print("       Run backend\\run.bat once first to create it, for a clean build env.")
    return sys.executable


def npm() -> str:
    """npm on Windows is npm.cmd, which CreateProcess won't find by bare name."""
    found = shutil.which("npm")
    if not found:
        raise SystemExit("npm not found on PATH - install Node.js (setup_prereqs.ps1) and re-run.")
    return found


def build_frontend() -> None:
    print("\n== Building frontend (npm run build) ==")
    npm_exe = npm()
    if not (FRONTEND_DIR / "node_modules").exists():
        run([npm_exe, "install"], cwd=FRONTEND_DIR)
    run([npm_exe, "run", "build"], cwd=FRONTEND_DIR)
    if not (FRONTEND_DIR / "dist" / "index.html").exists():
        raise SystemExit("frontend build did not produce frontend/dist/index.html")


# Everything in the dist folder that this script produces, and may therefore
# replace. Anything else there belongs to whoever has been using the app -
# data/ (the track database, generated audio, the voice library), logs/, and
# an .env they may have edited - and a rebuild must leave it alone.
BUILD_OUTPUTS = (f"{DIST_NAME}.exe", "_internal", "frontend_dist", "cashout_engine.exe")


def require_app_not_running() -> None:
    """PyInstaller can't replace a running exe, and finding that out halfway
    through a rebuild leaves a half-written folder."""
    exe = DIST_DIR / f"{DIST_NAME}.exe"
    if not exe.exists():
        return
    try:
        with open(exe, "ab"):
            pass
    except PermissionError:
        raise SystemExit(f"{exe} is in use - close the running app and try again.") from None


def clear_build_outputs() -> None:
    for name in BUILD_OUTPUTS:
        target = DIST_DIR / name
        # Windows keeps the image of a just-closed exe locked for a moment
        # after the process is gone, so "close the app, then rebuild" would
        # otherwise fail on a race the user can do nothing about.
        for attempt in range(10):
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                break
            except PermissionError:
                if attempt == 9:
                    raise SystemExit(f"{target} is still in use - close Cashout Studio and try again.") from None
                time.sleep(0.5)


def build_app_exe(console: bool) -> None:
    print("\n== Freezing the app (PyInstaller --onedir) ==")
    python = backend_python()
    run([python, "-m", "pip", "install", "--upgrade", "-r", "requirements.txt", "pyinstaller"], cwd=BACKEND_DIR)

    # --collect-submodules app: app.main:app is only ever referenced as a
    # dynamic string (uvicorn.run("app.main:app", ...)) so PyInstaller's
    # static import scan can't see it on its own - this forces the whole
    # backend/app package in regardless.
    # --collect-all on uvicorn/fastapi/starlette/httpx/certifi/multipart:
    # each does its own dynamic/lazy imports (uvicorn's protocol/loop/lifespan
    # implementations, starlette's optional multipart-parsing path used by
    # routes_yue2_upload.py, certifi's CA bundle data file) that static
    # analysis alone would miss.
    # --collect-all webview: pywebview loads its platform backend by name at
    # runtime and ships the WebView2 interop DLLs as package data.
    args = [
        python, "-m", "PyInstaller",
        "--name", DIST_NAME,
        "--onedir",
        "--noconfirm",
        "--clean",
        "--console" if console else "--windowed",
        "--distpath", str(PYINSTALLER_DISTPATH),
        "--workpath", str(PYINSTALLER_WORKPATH),
        "--specpath", str(PYINSTALLER_WORKPATH),
        "--collect-submodules", "app",
        "--collect-all", "uvicorn",
        "--collect-all", "fastapi",
        "--collect-all", "starlette",
        "--collect-all", "httpx",
        "--collect-all", "certifi",
        "--collect-all", "multipart",
        "--collect-all", "webview",
        "run_desktop.py",
    ]
    run(args, cwd=BACKEND_DIR)

    staged = PYINSTALLER_DISTPATH / DIST_NAME
    if not (staged / f"{DIST_NAME}.exe").exists():
        raise SystemExit(f"PyInstaller finished but {staged / f'{DIST_NAME}.exe'} is missing - check the log above.")

    print(f"\n== Installing build outputs into {DIST_DIR} ==")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    clear_build_outputs()
    for name in BUILD_OUTPUTS:
        source = staged / name
        if source.exists():
            shutil.move(str(source), str(DIST_DIR / name))


def collect_frontend_dist() -> None:
    print("\n== Copying frontend/dist -> CashoutStudio/frontend_dist ==")
    src = FRONTEND_DIR / "dist"
    if not src.exists():
        raise SystemExit("frontend/dist not found - run without --skip-frontend first.")
    dest = DIST_DIR / "frontend_dist"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def collect_native_engine() -> None:
    """Brings the native audio engine along, when it has been built.

    native_engine.engine_executable() looks for it beside the app's own exe
    in a frozen build, so that is where it has to land. Optional rather than
    required: the studio runs without it, just on the browser audio path,
    and failing a package build because somebody has not run engine/build.ps1
    would be the wrong trade.
    """
    print("\n== Copying the native audio engine ==")
    for candidate in (
        ROOT / "engine" / "build" / "Release" / "cashout_engine.exe",
        ROOT / "engine" / "build-core" / "Release" / "cashout_engine.exe",
    ):
        if candidate.is_file():
            shutil.copy2(candidate, DIST_DIR / "cashout_engine.exe")
            print(f"[ok] {candidate.name} -> {DIST_DIR / 'cashout_engine.exe'}")
            return
    print("[skip] engine not built - run engine/build.ps1 to include it.")
    print("       Without it the DAW's Audio engine panel offers nothing to start.")


def collect_env_file() -> None:
    print("\n== Copying backend/.env -> CashoutStudio/.env ==")
    src = BACKEND_DIR / ".env"
    dest = DIST_DIR / ".env"
    if dest.exists():
        # The packaged app's .env is a config file someone may have tuned for
        # the machine it runs on; a rebuild has no business overwriting it.
        print(f"[skip] {dest} already exists - leaving it as-is.")
        return
    if not src.exists():
        print("[warn] backend/.env not found - run setup_models.ps1 first (it writes this file).")
        print("       Without it the app falls back to external/ next to this folder.")
        return
    shutil.copy2(src, dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-frontend", action="store_true", help="reuse an existing frontend/dist build")
    parser.add_argument("--console", action="store_true", help="build with a console window attached (debugging)")
    args = parser.parse_args()

    require_app_not_running()
    if not args.skip_frontend:
        build_frontend()
    build_app_exe(console=args.console)
    collect_frontend_dist()
    collect_native_engine()
    collect_env_file()

    print(f"\nDone. App folder: {DIST_DIR}")
    print(f"It reads the engines from {ROOT / 'external'} via backend/.env - to move this")
    print(f"install elsewhere, copy {DIST_NAME}\\ and external\\ together and fix the paths in .env.")
    print(f"Launch by double-clicking {DIST_NAME}\\{DIST_NAME}.exe.")


if __name__ == "__main__":
    main()
