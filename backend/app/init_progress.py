"""Reading model-download progress out of an engine's log.

ACE-Step fetches roughly 10 GB of checkpoints from HuggingFace the first time
it initializes, and reports that only as tqdm bars on its own stdout - which
the orchestrator captures to logs/ace_step_api.log. Without parsing them back
out, the UI has nothing to show but a spinner, and a 45-minute download is
indistinguishable from a hang.
"""
from __future__ import annotations

import re

from .config import LOG_DIR

# e.g. "model.safetensors:  49%|####8     | 2.34G/4.79G [10:06<17:03, 2.39MB/s]"
_PROGRESS_LINE = re.compile(
    r"(?P<file>[\w.\-]+):\s+(?P<percent>\d+)%\|[^|]*\|\s*"
    r"(?P<done>[\d.]+[KMGT]?)/(?P<total>[\d.]+[KMGT]?)\s*"
    r"\[(?P<elapsed>[\d:]+)<(?P<eta>[\d:?]+),\s*(?P<rate>[^\],\s]+)"
)


def download_progress(log_name: str, lines: int = 400) -> list[dict]:
    """Latest state of each file currently downloading, newest line wins.

    Several files download at once and tqdm rewrites each bar in place, so the
    log holds hundreds of stale copies - only the last line per file counts.
    """
    path = LOG_DIR / f"{log_name}.log"
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    latest: dict[str, dict] = {}
    for line in content.splitlines()[-lines:]:
        for match in _PROGRESS_LINE.finditer(line):
            entry = match.groupdict()
            # tqdm is also used for counts ("files: 25/28, 2.50it/s"), which
            # say nothing about how long a 10 GB download has left. Only bars
            # measured in bytes carry a unit suffix.
            if not entry["total"][-1:].isalpha():
                continue
            # Two different repos both ship a "model.safetensors"; the total
            # size is what tells their progress bars apart.
            latest[f"{entry['file']}:{entry['total']}"] = {
                "file": entry["file"],
                "percent": int(entry["percent"]),
                "downloaded": entry["done"],
                "total": entry["total"],
                "eta": entry["eta"],
                "rate": entry["rate"],
            }
    return list(latest.values())
