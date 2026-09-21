"""The file browser's backend.

Browsing is confined to a set of roots rather than the whole disk, for two
reasons. The obvious one is that this is an HTTP API, and once the studio
listens on anything but loopback - which multiplayer will need - an
unrestricted directory lister is a way to read someone's whole drive from
another machine on the network. The less obvious one is that it is simply
better: FL Studio's browser shows configured search paths, not C:\\, because
a tree rooted at the drive is useless for finding a kick.

Roots come from three places, in order: the studio's own folders, the
obvious system ones, and whatever the user adds. Every path that comes back
over the API is checked against them again on the way in - a root list that
is only enforced when building the tree is not enforced at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import json

from .config import ACE_STEP_DIR, DATA_DIR, VOICES_DIR

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".aif", ".aiff"}
MIDI_EXT = {".mid", ".midi"}
BROWSABLE = AUDIO_EXT | MIDI_EXT

# Directories that are always noise in a music browser.
SKIP_NAMES = {
    "__pycache__", "node_modules", ".git", ".venv", "venv", "$RECYCLE.BIN",
    "System Volume Information", ".cache", "site-packages",
}

# Folders the user added, kept on disk rather than in memory. A browser
# that forgets where your sample library is every time the app restarts is
# a browser nobody configures twice.
_ROOTS_FILE = DATA_DIR / "browser_roots.json"


def _load_extra_roots() -> list[Path]:
    try:
        raw = json.loads(_ROOTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    # Missing folders are kept rather than dropped: an external drive that is
    # unplugged today is still where the samples live, and silently forgetting
    # it would be worse than showing it greyed out.
    return [Path(str(entry)) for entry in raw if str(entry).strip()]


def _save_extra_roots() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _ROOTS_FILE.write_text(
            json.dumps([str(path) for path in _extra_roots], indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Not fatal - the roots stay for this session, they just will not
        # survive a restart.
        pass


_extra_roots: list[Path] = _load_extra_roots()


@dataclass
class Root:
    key: str
    label: str
    path: Path
    removable: bool = False

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "path": str(self.path),
            "removable": self.removable,
            "exists": self.path.is_dir(),
        }


def _home(*parts: str) -> Path:
    return Path(os.path.expanduser("~")).joinpath(*parts)


def roots() -> list[Root]:
    """Everywhere the browser is allowed to look."""
    built_in = [
        Root("library", "Library", DATA_DIR / "files"),
        Root("voices", "Voices", VOICES_DIR),
        Root("datasets", "Datasets", ACE_STEP_DIR / "datasets"),
        Root("music", "Music", _home("Music")),
        Root("downloads", "Downloads", _home("Downloads")),
        Root("desktop", "Desktop", _home("Desktop")),
    ]
    extra = [
        Root(f"user{index}", path.name or str(path), path, removable=True)
        for index, path in enumerate(_extra_roots)
    ]
    # Built-in roots are hidden when absent (an empty "Voices" entry helps
    # nobody), but a folder the user chose is always shown - `exists` tells
    # the UI to grey it out if the drive is unplugged.
    return [root for root in built_in if root.path.is_dir()] + extra


def add_root(path: str) -> Optional[Root]:
    candidate = Path(path).expanduser()
    if not candidate.is_dir():
        return None
    resolved = candidate.resolve()
    if any(resolved == existing.resolve() for existing in _extra_roots):
        return None
    _extra_roots.append(resolved)
    _save_extra_roots()
    return Root(f"user{len(_extra_roots) - 1}", resolved.name or str(resolved), resolved, True)


def remove_root(path: str) -> bool:
    target = Path(path).expanduser().resolve()
    for index, existing in enumerate(_extra_roots):
        if existing.resolve() == target:
            _extra_roots.pop(index)
            _save_extra_roots()
            return True
    return False


def within_roots(path: Path) -> bool:
    """Whether a path is inside any allowed root.

    Resolved on both sides before comparing, so a `..` in the request or a
    symlink pointing out of a root cannot walk past the boundary.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots():
        try:
            resolved.relative_to(root.path.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


@dataclass
class Entry:
    name: str
    path: Path
    is_dir: bool
    size: int
    kind: str   # "dir" | "audio" | "midi"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "is_dir": self.is_dir,
            "size": self.size,
            "kind": self.kind,
        }


def _kind(path: Path) -> Optional[str]:
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in MIDI_EXT:
        return "midi"
    return None


def listing(path: Path, limit: int = 800) -> list[Entry]:
    """One directory, folders first, only files worth auditioning.

    Not recursive: a browser that walks a sample library on every click is
    how you freeze the UI on someone's 40,000-file Splice folder.
    """
    entries: list[Entry] = []
    directories: list[Entry] = []
    try:
        with os.scandir(path) as scan:
            for item in scan:
                if len(entries) + len(directories) >= limit:
                    break
                if item.name.startswith(".") or item.name in SKIP_NAMES:
                    continue
                try:
                    if item.is_dir(follow_symlinks=False):
                        directories.append(Entry(item.name, Path(item.path), True, 0, "dir"))
                        continue
                    kind = _kind(Path(item.name))
                    if not kind:
                        continue
                    entries.append(
                        Entry(item.name, Path(item.path), False, item.stat().st_size, kind)
                    )
                except OSError:
                    continue
    except (OSError, PermissionError):
        return []

    directories.sort(key=lambda entry: entry.name.lower())
    entries.sort(key=lambda entry: entry.name.lower())
    return directories + entries


def search(term: str, limit: int = 200) -> list[Entry]:
    """Name search across every root.

    Depth-limited rather than exhaustive: this runs on a keystroke, and the
    point is to find the kick you half-remember, not to index the drive.
    """
    needle = term.strip().lower()
    if len(needle) < 2:
        return []

    found: list[Entry] = []
    for root in roots():
        for path in _walk(root.path, max_depth=4):
            if len(found) >= limit:
                return found
            if needle in path.name.lower():
                kind = _kind(path)
                if not kind:
                    continue
                try:
                    found.append(Entry(path.name, path, False, path.stat().st_size, kind))
                except OSError:
                    continue
    return found


def _walk(base: Path, max_depth: int, depth: int = 0) -> Iterable[Path]:
    if depth > max_depth:
        return
    try:
        with os.scandir(base) as scan:
            for item in scan:
                if item.name.startswith(".") or item.name in SKIP_NAMES:
                    continue
                try:
                    if item.is_dir(follow_symlinks=False):
                        yield from _walk(Path(item.path), max_depth, depth + 1)
                    else:
                        yield Path(item.path)
                except OSError:
                    continue
    except (OSError, PermissionError):
        return
