"""Talks to the native audio engine.

The engine is a separate process for a reason worth restating here: the
studio's front end is a web view, and a web view stops the world to do
layout. An audio thread in that process glitches every time somebody opens a
panel. Out of process, on an MMCSS Pro Audio thread, it does not - measured
at 2.67ms output latency with zero dropouts while the UI was being driven.

This module owns the process and the socket. It deliberately does not own
any audio logic: everything here is a command on the wire, so the engine
stays the single place that knows about sample rates and buffers.
"""
from __future__ import annotations

import collections
import ctypes
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Deque, Optional

from .config import DATA_DIR

# The engine binds loopback only and has no authentication, which is correct
# for something that is an implementation detail of one machine's studio.
# It must never be reachable from the collaboration listener.
ENGINE_HOST = "127.0.0.1"

# Preferred, not fixed. See _free_port.
ENGINE_PORT = 9310


def _free_port(preferred: int) -> int:
    """The preferred port when it is free, otherwise any free one.

    A hard-coded port made the studio permanently unable to start its engine
    whenever anything else held 9310 - most often a previous engine that
    outlived its parent, which the job object below now prevents, but also a
    second copy of the studio or an unrelated program. The failure was
    total and the message was "the engine exited immediately", which tells
    the user nothing they can act on.
    """
    with socket.socket() as probe:
        try:
            probe.bind((ENGINE_HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as probe:
        probe.bind((ENGINE_HOST, 0))
        return int(probe.getsockname()[1])


_job_handle: Optional[int] = None
_job_lock = threading.Lock()


def _kill_engine_with_studio(process: subprocess.Popen) -> None:
    """Ties the engine's lifetime to this process, hard kills included.

    `stop()` terminates the engine on a clean shutdown, but nothing runs
    when the studio is killed from Task Manager or crashes - and the engine
    it leaves behind still holds the port and, worse, still holds the audio
    device in exclusive mode. This puts every engine we spawn into a Windows
    job object marked KILL_ON_JOB_CLOSE: when this process dies by any
    means, the kernel closes the last handle to the job and takes the
    children with it.

    Best effort throughout. A machine where this does not work is a machine
    that behaves exactly as it did before, so nothing here is allowed to
    stop the engine from starting.
    """
    if sys.platform != "win32":
        return
    global _job_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        with _job_lock:
            if _job_handle is None:
                handle = kernel32.CreateJobObjectW(None, None)
                if not handle:
                    return

                class _BasicLimits(ctypes.Structure):
                    _fields_ = [
                        ("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32),
                    ]

                class _IoCounters(ctypes.Structure):
                    _fields_ = [(name, ctypes.c_uint64) for name in (
                        "ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount",
                        "WriteTransferCount", "OtherTransferCount",
                    )]

                class _ExtendedLimits(ctypes.Structure):
                    _fields_ = [
                        ("BasicLimitInformation", _BasicLimits),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t),
                    ]

                limits = _ExtendedLimits()
                limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
                if not kernel32.SetInformationJobObject(
                    ctypes.c_void_p(handle),
                    9,  # JobObjectExtendedLimitInformation
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                ):
                    kernel32.CloseHandle(ctypes.c_void_p(handle))
                    return
                # Deliberately never closed: the handle staying open for the
                # life of this process is the mechanism.
                _job_handle = handle

        # 0x0100 PROCESS_SET_QUOTA | 0x0001 PROCESS_TERMINATE
        child = kernel32.OpenProcess(0x0100 | 0x0001, False, process.pid)
        if not child:
            return
        try:
            kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(_job_handle), ctypes.c_void_p(child)
            )
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(child))
    except (OSError, AttributeError):
        # No job object, so an orphan is possible again - which is the
        # behaviour that shipped before this, not a regression.
        pass

_APP_ROOT = Path(__file__).resolve().parents[2]


def engine_executable() -> Optional[Path]:
    """Where the built engine is, in a checkout or beside a packaged build."""
    candidates = [
        _APP_ROOT / "engine" / "build" / "Release" / "cashout_engine.exe",
        _APP_ROOT / "engine" / "build-core" / "Release" / "cashout_engine.exe",
        Path(sys.executable).parent / "cashout_engine.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class EngineUnavailable(RuntimeError):
    pass


class NativeEngine:
    """One engine process, and a socket to it.

    Commands are serialised behind a lock. The protocol is one line in, one
    line out, so two callers interleaving would read each other's replies -
    and the cost of the lock is nothing next to the fact that these are
    control messages, not audio.
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._socket: Optional[socket.socket] = None
        self._file: Optional[Any] = None
        self._lock = threading.Lock()
        self._port = ENGINE_PORT
        # The engine's own output, drained by a reader thread. Bounded
        # because nothing consumes it in the happy path, and an unread pipe
        # that fills up blocks the writer - which would hang the engine
        # mid-session rather than at startup, the worst kind of bug to find.
        self._log: Deque[str] = collections.deque(maxlen=200)
        self.last_error = ""

    # ------------------------------------------------------------- process

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        if self.running and self._socket is not None:
            return True

        exe = engine_executable()
        if exe is None:
            self.last_error = (
                "the native engine is not built - run engine/build.ps1, "
                "or open engine/build/cashout_engine.sln in Visual Studio"
            )
            return False

        self.stop()
        self._log.clear()
        self._port = _free_port(ENGINE_PORT)
        try:
            self._process = subprocess.Popen(
                [str(exe), "--port", str(self._port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # No console window when the studio is launched from its
                # shortcut rather than a terminal.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.last_error = f"could not start the engine: {exc}"
            return False

        _kill_engine_with_studio(self._process)
        self._drain_output(self._process)

        # Readiness is a round trip to the engine, not a line of log text.
        # The line was read with a blocking readline() inside a loop that
        # checked a deadline it could never reach: an engine that came up
        # silently and stayed up would park that call forever, and the
        # request that triggered it with it.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self.last_error = self._exit_reason()
                return False
            if self._handshake():
                break
            time.sleep(0.05)
        else:
            self.last_error = "the engine did not come up in ten seconds"
            self.stop()
            return False

        assert self._socket is not None
        self._socket.settimeout(None)
        self.last_error = ""
        return True

    def _handshake(self) -> bool:
        """Connects, and proves the thing that answered is the engine.

        Connecting alone is not readiness. Measured: with an unrelated
        listener squatting on the port, a connect-only check reported the
        engine as started and handed back a socket wired to the squatter -
        every later command would then fail in a way that looked like the
        engine misbehaving. One ping costs a millisecond and settles both
        questions at once, so it is the readiness check.
        """
        sock = None
        try:
            sock = socket.create_connection((ENGINE_HOST, self._port), timeout=2)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            handle = sock.makefile("rw", encoding="utf-8", newline="\n")
            handle.write(json.dumps({"cmd": "ping"}) + "\n")
            handle.flush()
            reply = json.loads(handle.readline() or "null")
        except (OSError, ValueError):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            return False

        if not isinstance(reply, dict) or "ok" not in reply:
            try:
                sock.close()
            except OSError:
                pass
            return False

        self._socket = sock
        self._file = handle
        return True

    def _drain_output(self, process: subprocess.Popen) -> None:
        """Keeps the engine's pipe empty, and its last words available."""
        if process.stdout is None:
            return

        def pump() -> None:
            try:
                for line in process.stdout:  # type: ignore[union-attr]
                    self._log.append(line.rstrip())
            except (OSError, ValueError):
                pass

        threading.Thread(target=pump, name="engine-log", daemon=True).start()

    def _exit_reason(self) -> str:
        """Why the engine died, in the engine's own words where it said so.

        "the engine exited immediately" was the whole message before this,
        and it is the least useful sentence available: the process printed
        the actual reason to a pipe that was then thrown away. The common
        cause - a port already held by a previous engine - was completely
        invisible, which is why it took a packet capture to find rather than
        a glance.
        """
        # The pump is a separate thread and the process has only just gone;
        # give it a moment to catch the final lines before quoting it.
        for _ in range(20):
            if self._log:
                break
            time.sleep(0.01)
        code = self._process.returncode if self._process else None
        said = "; ".join(line for line in list(self._log)[-3:] if line)
        detail = f" - {said}" if said else ""
        return f"the engine exited immediately (code {code}){detail}"

    def stop(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
                self._file = None
            if self._socket is not None:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
            if self._process is not None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        self._process.kill()
                    except OSError:
                        pass
                self._process = None

    # ------------------------------------------------------------- commands

    def call(self, **command: Any) -> dict:
        """One command, one reply. Raises EngineUnavailable if it is not up."""
        if not self.running or self._file is None:
            if not self.start():
                raise EngineUnavailable(self.last_error or "the engine is not running")

        with self._lock:
            assert self._file is not None
            try:
                self._file.write(json.dumps(command) + "\n")
                self._file.flush()
                line = self._file.readline()
            except OSError as exc:
                # A dead socket means a dead engine; drop it so the next call
                # starts a fresh one rather than failing forever.
                self.stop()
                raise EngineUnavailable(f"the engine stopped responding: {exc}") from None

        if not line:
            self.stop()
            raise EngineUnavailable("the engine closed the connection")
        try:
            return json.loads(line)
        except ValueError:
            raise EngineUnavailable("the engine sent something that was not JSON") from None

    def status(self) -> dict:
        if not self.running:
            return {
                "ok": False,
                "available": engine_executable() is not None,
                "running": False,
                "error": self.last_error,
            }
        try:
            state = self.call(cmd="status")
        except EngineUnavailable as exc:
            return {"ok": False, "available": True, "running": False, "error": str(exc)}
        state["available"] = True
        state["running"] = True
        return state


engine = NativeEngine()


def capture_dir() -> Path:
    directory = DATA_DIR / "captures"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
