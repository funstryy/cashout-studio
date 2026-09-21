"""Machine telemetry for the dashboard.

Everything here is measured. A workstation dashboard that draws a confident
GPU ring from a made-up number is worse than one that shows nothing: the
whole point of the widget is to answer "can I start a generation right now",
and a decorative gauge answers it wrongly.

Where the numbers come from, and why these sources:

  GPU load    Windows performance counters (\\GPU Engine(*)\\Utilization
              Percentage). Vendor-neutral - it reads the same on AMD, NVIDIA
              and Intel, which matters because the studio ships for all
              three. nvidia-smi would have been easier and would report
              nothing on the machine this was built on.
  VRAM        \\GPU Adapter Memory(*)\\Dedicated Usage for what is in use.
              The total comes from the driver's registry entry, not from
              WMI's AdapterRAM: that field is a 32-bit DWORD and reports
              4095 MB for every card with 4 GB or more. A 6 GB card showing
              "1.7 of 4.1 GB" would be quietly wrong in the direction that
              matters, since running out of VRAM is the failure this widget
              exists to predict.
  CPU / RAM   psutil.
  Disk        the volume holding the studio's own data directory, because
              that is the one that fills up with generated audio.

The GPU probes shell out to PowerShell and cost about a second, so they are
cached. A dashboard polling every few seconds must not put a second of
PowerShell on every request.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from typing import Optional

from .config import DATA_DIR

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

# Long enough that polling is cheap, short enough that starting a generation
# shows up before the user wonders whether the click registered.
_GPU_TTL = 2.5
_ADAPTER_TTL = 300.0   # the card does not change while the app is running

_gpu_cache: tuple[float, dict] = (0.0, {})
_adapter_cache: tuple[float, dict] = (0.0, {})
_probe_lock = asyncio.Lock()


async def _powershell(script: str, timeout: float = 8.0) -> str:
    if not IS_WINDOWS:
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", "replace").strip()
    except (asyncio.TimeoutError, OSError):
        return ""


async def _adapter() -> dict:
    """Card name, driver and true VRAM size. Cached for the session."""
    global _adapter_cache
    cached_at, value = _adapter_cache
    if value and time.time() - cached_at < _ADAPTER_TTL:
        return value

    script = r"""
$c = Get-CimInstance Win32_VideoController |
     Sort-Object -Property AdapterRAM -Descending | Select-Object -First 1
# qwMemorySize is the 64-bit truth; AdapterRAM saturates at 4095 MB.
$total = 0
$root = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
Get-ChildItem $root -EA SilentlyContinue | ForEach-Object {
  $q = (Get-ItemProperty $_.PSPath -Name 'HardwareInformation.qwMemorySize' -EA SilentlyContinue).'HardwareInformation.qwMemorySize'
  if ($q -and $q -gt $total) { $total = $q }
}
if (-not $total) { $total = $c.AdapterRAM }
"$($c.Name)|$($c.DriverVersion)|$total"
"""
    raw = await _powershell(script)
    parts = raw.split("|") if raw else []
    result = {
        "name": parts[0].strip() if len(parts) > 0 and parts[0].strip() else None,
        "driver": parts[1].strip() if len(parts) > 1 and parts[1].strip() else None,
        "vram_total_bytes": int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else None,
    }
    _adapter_cache = (time.time(), result)
    return result


async def _gpu_live() -> dict:
    """Utilisation and VRAM in use, cached briefly."""
    global _gpu_cache
    cached_at, value = _gpu_cache
    if value and time.time() - cached_at < _GPU_TTL:
        return value

    script = r"""
$u = (Get-Counter '\GPU Engine(*)\Utilization Percentage' -EA SilentlyContinue).CounterSamples |
     Measure-Object CookedValue -Sum
$m = (Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -EA SilentlyContinue).CounterSamples |
     Measure-Object CookedValue -Sum
"$([math]::Round($u.Sum,1))|$([int64]$m.Sum)"
"""
    raw = await _powershell(script)
    parts = raw.split("|") if raw else []

    def number(index: int) -> Optional[float]:
        try:
            return float(parts[index])
        except (IndexError, ValueError):
            return None

    utilisation = number(0)
    result = {
        # Counters are summed across every GPU engine (3D, copy, compute,
        # video), so the total can exceed 100 when several are busy at once.
        "utilisation": min(100.0, utilisation) if utilisation is not None else None,
        "vram_used_bytes": int(number(1)) if number(1) is not None else None,
    }
    _gpu_cache = (time.time(), result)
    return result


def _cpu_and_memory() -> dict:
    try:
        import psutil
    except ImportError:
        return {"cpu_percent": None, "ram_used_bytes": None, "ram_total_bytes": None}

    memory = psutil.virtual_memory()
    return {
        # interval=None returns the load since the last call rather than
        # blocking for a sample window; the dashboard polls often enough that
        # this is meaningful and it keeps the request instant.
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_used_bytes": memory.total - memory.available,
        "ram_total_bytes": memory.total,
    }


def _disk() -> dict:
    """The volume the studio writes to, which is the one that fills up."""
    try:
        target = DATA_DIR if DATA_DIR.exists() else DATA_DIR.parent
        usage = shutil.disk_usage(target)
        return {"disk_free_bytes": usage.free, "disk_total_bytes": usage.total, "disk_path": str(target)}
    except OSError:
        return {"disk_free_bytes": None, "disk_total_bytes": None, "disk_path": None}


async def snapshot() -> dict:
    """One reading of everything, for the dashboard."""
    async with _probe_lock:
        adapter, live = await asyncio.gather(_adapter(), _gpu_live())

    used = live.get("vram_used_bytes")
    total = adapter.get("vram_total_bytes")
    return {
        "gpu": {
            "name": adapter.get("name"),
            "driver": adapter.get("driver"),
            "utilisation": live.get("utilisation"),
            "vram_used_bytes": used,
            "vram_total_bytes": total,
            "vram_percent": round(100 * used / total, 1) if used and total else None,
        },
        **_cpu_and_memory(),
        **_disk(),
        "measured": True,
    }
