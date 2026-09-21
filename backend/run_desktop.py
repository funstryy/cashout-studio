"""Desktop entry point: the backend and its UI in one window, one process.

Starts the same FastAPI app dev.bat serves (app.main:app) on a loopback port
in a background thread, then shows it in a native WebView2 window - so the
packaged build behaves like a desktop program rather than a server you open a
browser tab for. Nothing about the app itself changes; only the shell does.

Used both as `python run_desktop.py` in a checkout and as the frozen entry
point of CashoutStudio.exe (see build_dist.py).
"""
from __future__ import annotations

import argparse
import os
import ctypes
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from app.config import LOG_DIR

DEFAULT_PORT = 9000
WINDOW_TITLE = "Cashout Studio"

IS_WINDOWS = sys.platform == "win32"
SINGLE_INSTANCE_NAME = "CashoutStudio.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9

# Held for the process's lifetime: releasing the handle releases the mutex,
# and the next launch would start a second copy.
_instance_mutex = None


def _redirect_std_streams() -> None:
    """A --windowed PyInstaller build has no console: sys.stdout/stderr are
    None there, and anything that writes to them (uvicorn's logging, any
    traceback) would fail. Point them at a file instead, before uvicorn's
    logging config captures them."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(LOG_DIR / "desktop.log", "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = handle
    sys.stderr = handle


def _claim_single_instance() -> bool:
    """False when another Cashout Studio instance already has the app open.

    Two copies cannot coexist: they share one WebView2 user-data folder, and
    the second one taking it tears the first one's window down - which ends
    that instance's backend and leaves any window still pointing at it unable
    to reach the API at all. Better to hand the user the window they already
    have than to start a copy that kills it.
    """
    global _instance_mutex
    if not IS_WINDOWS:
        return True
    _instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_NAME)
    return ctypes.windll.kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


def _focus_existing_window() -> None:
    if not IS_WINDOWS:
        return
    user32 = ctypes.windll.user32
    handle = user32.FindWindowW(None, WINDOW_TITLE)
    if handle:
        user32.ShowWindow(handle, _SW_RESTORE)
        user32.SetForegroundWindow(handle)


def _resolve_port(preferred: int) -> int:
    """Use the preferred port when it's free, otherwise any free port - a
    stale backend from a previous run (or anything else on 9000) shouldn't
    stop the app from starting."""
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_serving(port: int, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    # Loopback only, always. A collaboration session opens a second listener
    # on the VPN address for as long as it runs; see app/collab.py.
    from app import collab

    collab.served_port = port
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port))
    # Not a daemon thread: the app's lifespan shutdown is what stops the
    # model subprocesses, so this thread has to be allowed to finish.
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=False)
    thread.start()
    return server, thread


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    # Generous: shutdown tears down whichever model process tree is still
    # running, and those get a graceful CTRL_BREAK window first.
    thread.join(timeout=60.0)


def _show_window(url: str) -> bool:
    """Open the native window, blocking until the user closes it. False if no
    webview backend is usable on this machine (e.g. the WebView2 runtime
    isn't installed), so the caller can fall back to a browser."""
    # Chromium refuses to autoplay media with sound until the user has
    # interacted with the page, which for a startup animation means it never
    # plays at all - measured: the video buffers to readyState 4 and then
    # sits paused. In a browser that policy protects people from pages they
    # did not ask to open; in a desktop app the user launched deliberately,
    # it only breaks the splash. Set before importing webview so the runtime
    # picks it up when it creates the environment.
    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--autoplay-policy=no-user-gesture-required",
    )

    try:
        import webview
    except ImportError:
        print("[desktop] pywebview is not installed - falling back to a browser window.")
        return False

    try:
        webview.create_window(WINDOW_TITLE, url, width=1500, height=950, min_size=(1024, 700))
        webview.start()
        return True
    except Exception as exc:  # noqa: BLE001 - any webview failure must fall back, not crash
        print(f"[desktop] could not open a native window ({exc}) - falling back to a browser.")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cashout Studio as a desktop app.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="serve only, don't open a window (the URL is printed instead)",
    )
    args = parser.parse_args()

    _redirect_std_streams()
    # --no-window is the headless/dev path and doesn't own a window, so it is
    # free to run alongside an open app on another port.
    if not args.no_window and not _claim_single_instance():
        print("[desktop] Cashout Studio is already running - focusing that window.")
        _focus_existing_window()
        return

    port = _resolve_port(args.port)
    url = f"http://127.0.0.1:{port}"
    server, thread = _start_server(port)

    if not _wait_until_serving(port):
        print(f"[desktop] backend did not start listening on {url} - see logs/ for why.")
        _stop_server(server, thread)
        raise SystemExit(1)

    print(f"[desktop] backend ready on {url}")
    if args.no_window:
        try:
            thread.join()
        except KeyboardInterrupt:
            _stop_server(server, thread)
        return

    if not _show_window(url):
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass

    _stop_server(server, thread)


if __name__ == "__main__":
    main()
