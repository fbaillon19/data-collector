"""Per-device polling state: consecutive failures, last alert times, last
known `overwritten` count. Kept as a JSON file next to the device's archive
directory, outside version control (CLAUDE.md §3, .gitignore).

The archive is authoritative for what was collected; state never is, and
losing it is survivable — but the cost is exact, not a vague reassurance.
If the state file is lost, only last_ts_utc is recovered, from the
archive's last line; everything else resets to its default. Two distinct
failures follow from that, not one:

- Mid-episode (an unreachable device, a muted sensor), the dedup markers
  reset: the next poll can log a spurious duplicate `_start` event and
  re-fire an alert for a condition that was already known and already
  reported.
- Between two polls, `last_overwritten` resets to None: the next
  comparison has no baseline, so an increase in `overwritten` that
  happened during that gap is silently absorbed — never logged in
  events.csv, never alerted. A real loss can go unrecorded.

State is disposable by design. That does not make it free to lose.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from collector import archive

ALERT_KINDS = ("unreachable", "overwritten", "mute_sensor", "time_untrusted")


def state_path(archive_dir: Path, device: str) -> Path:
    return Path(archive_dir) / f"{device}.state.json"


def default_state() -> Dict[str, Any]:
    return {
        "last_ts_utc": None,
        "last_overwritten": None,
        "consecutive_failures": 0,
        "last_alerts": {kind: None for kind in ALERT_KINDS},
    }


def rebuild(archive_dir: Path, device: str) -> Dict[str, Any]:
    """What survives when the state file is missing or corrupt: only
    last_ts_utc. See the module docstring for what does not, and at what
    cost.
    """
    state = default_state()
    state["last_ts_utc"] = archive.last_archived_timestamp(Path(archive_dir), device)
    return state


def load(archive_dir: Path, device: str) -> Dict[str, Any]:
    path = state_path(archive_dir, device)
    if not path.is_file():
        return rebuild(archive_dir, device)
    try:
        with path.open(encoding="ascii") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return rebuild(archive_dir, device)

    state.setdefault("last_ts_utc", None)
    state.setdefault("last_overwritten", None)
    state.setdefault("consecutive_failures", 0)
    alerts = state.setdefault("last_alerts", {})
    for kind in ALERT_KINDS:
        alerts.setdefault(kind, None)
    return state


def save(archive_dir: Path, device: str, state: Dict[str, Any]) -> None:
    path = state_path(archive_dir, device)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="ascii") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
