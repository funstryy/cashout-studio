"""Finding the VST plugins installed on this machine.

This only inventories what is on disk - it does not load anything. A VST3 is
native machine code, so reading a plugin's real class names, parameters or
audio I/O means loading its DLL into a process, which is the job of the
out-of-process host (see plugin_host.py). Keeping discovery separate mirrors
how every serious DAW does it: FL Studio ships ILPluginScanner64.exe for
exactly this reason, because a malformed plugin should take down a scanner,
not the studio.

Layout notes, from what is actually installed here:
  - A VST3 is usually a *bundle directory*: Name.vst3/Contents/x86_64-win/Name.vst3,
    where the inner file is the DLL. It can also be a bare .vst3 DLL.
  - Vendors nest bundles in subfolders (VST3/Antares/Auto-Tune Pro.vst3), so
    the scan has to recurse rather than list one level.
  - moduleinfo.json (SDK 3.7.5+) carries class names and would save loading
    the binary, but most shipping plugins still don't include one.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

VST3_SUFFIX = ".vst3"
WINDOWS_VST3_ARCH_DIR = "x86_64-win"

DEFAULT_VST3_DIRS = (
    r"C:\Program Files\Common Files\VST3",
    r"C:\Program Files (x86)\Common Files\VST3",
)
DEFAULT_VST2_DIRS = (
    r"C:\Program Files\VstPlugins",
    r"C:\Program Files\Steinberg\VSTPlugins",
    r"C:\Program Files\Common Files\VST2",
)


@dataclass
class PluginInfo:
    name: str
    format: str            # "vst3" | "vst2"
    path: str              # the binary the host will load
    bundle_path: str       # what the user recognises in Explorer
    size_mb: float
    arch: str
    class_names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "format": self.format,
            "path": self.path,
            "bundle_path": self.bundle_path,
            "size_mb": self.size_mb,
            "arch": self.arch,
            "class_names": self.class_names,
        }


def _scan_dirs(kind: str) -> list[Path]:
    """Scan roots, overridable for people who keep plugins elsewhere."""
    override = os.getenv(f"{kind.upper()}_DIRS", "")
    if override.strip():
        return [Path(p.strip()) for p in override.split(os.pathsep) if p.strip()]
    defaults = DEFAULT_VST3_DIRS if kind == "vst3" else DEFAULT_VST2_DIRS
    return [Path(p) for p in defaults]


def _read_module_info(bundle: Path) -> list[str]:
    info = bundle / "Contents" / "moduleinfo.json"
    if not info.is_file():
        return []
    try:
        data = json.loads(info.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    return [c.get("name", "") for c in data.get("Classes", []) if c.get("name")]


def _vst3_binary(bundle: Path) -> tuple[Path, str] | None:
    """The DLL inside a bundle, plus its architecture."""
    arch_dir = bundle / "Contents" / WINDOWS_VST3_ARCH_DIR
    if arch_dir.is_dir():
        for candidate in arch_dir.glob(f"*{VST3_SUFFIX}"):
            return candidate, "x86_64"
        for candidate in arch_dir.glob("*.dll"):
            return candidate, "x86_64"
    # Some bundles only ship a 32-bit slice, which a 64-bit host cannot load
    # in-process - worth reporting rather than hiding.
    x86_dir = bundle / "Contents" / "x86-win"
    if x86_dir.is_dir():
        for candidate in x86_dir.glob(f"*{VST3_SUFFIX}"):
            return candidate, "x86"
    return None


def scan_vst3(roots: list[Path] | None = None) -> list[PluginInfo]:
    found: dict[str, PluginInfo] = {}
    for root in roots if roots is not None else _scan_dirs("vst3"):
        if not root.is_dir():
            continue
        for entry in root.rglob(f"*{VST3_SUFFIX}"):
            # rglob matches the inner DLL too, since it shares the suffix -
            # skip anything already inside a bundle we will report.
            if WINDOWS_VST3_ARCH_DIR in entry.parts or "x86-win" in entry.parts:
                continue
            if entry.is_dir():
                resolved = _vst3_binary(entry)
                if not resolved:
                    continue
                binary, arch = resolved
                classes = _read_module_info(entry)
            elif entry.is_file():
                binary, arch, classes = entry, "x86_64", []
            else:
                continue

            info = PluginInfo(
                name=entry.stem,
                format="vst3",
                path=str(binary),
                bundle_path=str(entry),
                size_mb=round(binary.stat().st_size / (1024 * 1024), 1),
                arch=arch,
                class_names=classes,
            )
            found.setdefault(info.path, info)
    return sorted(found.values(), key=lambda p: p.name.lower())


def scan_vst2(roots: list[Path] | None = None) -> list[PluginInfo]:
    """VST2 is inventory-only and will stay that way: Steinberg withdrew the
    VST2 SDK licence, so there is no lawful route to hosting these. Listing
    them is still better than pretending the folder is empty."""
    found: dict[str, PluginInfo] = {}
    for root in roots if roots is not None else _scan_dirs("vst2"):
        if not root.is_dir():
            continue
        for dll in root.rglob("*.dll"):
            info = PluginInfo(
                name=dll.stem,
                format="vst2",
                path=str(dll),
                bundle_path=str(dll),
                size_mb=round(dll.stat().st_size / (1024 * 1024), 1),
                arch="unknown",
            )
            found.setdefault(info.path, info)
    return sorted(found.values(), key=lambda p: p.name.lower())


def scan_all() -> dict:
    vst3 = scan_vst3()
    vst2 = scan_vst2()
    return {
        "vst3": [p.as_dict() for p in vst3],
        "vst2": [p.as_dict() for p in vst2],
        "searched": {
            "vst3": [str(p) for p in _scan_dirs("vst3")],
            "vst2": [str(p) for p in _scan_dirs("vst2")],
        },
    }
