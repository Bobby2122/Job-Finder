from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATE_VERSION = 2


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "discovered_ids": [],
        "recommended_ids": [],
        "last_seen_at": {},
        "first_discovered_at": {},
        "first_recommended_at": {},
        "last_run": None,
    }


def _normalize_state(data: dict[str, Any]) -> dict[str, Any]:
    state = _empty_state()
    legacy_seen = data.get("seen_ids", [])
    if isinstance(legacy_seen, list):
        seen = sorted(set(str(value) for value in legacy_seen))
        state["discovered_ids"] = seen
        state["recommended_ids"] = seen
    for key in (
        "discovered_ids",
        "recommended_ids",
    ):
        if isinstance(data.get(key), list):
            state[key] = sorted(set(str(value) for value in data[key]))
    for key in (
        "last_seen_at",
        "first_discovered_at",
        "first_recommended_at",
    ):
        if isinstance(data.get(key), dict):
            state[key] = {
                str(item_key): str(item_value)
                for item_key, item_value in data[key].items()
            }
    state["last_run"] = data.get("last_run")
    return state


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    return _normalize_state(data)


def recommendation_state(
    state: dict[str, Any],
    tracking_id: str,
    tracker_status: str = "New",
) -> str:
    normalized = tracker_status.strip()
    if normalized in {"Viewed", "Started", "Applied", "Rejected", "Not Interested"}:
        return normalized.upper()
    if tracking_id in set(str(value) for value in state.get("recommended_ids", [])):
        return "PREVIOUSLY RECOMMENDED"
    if tracking_id in set(str(value) for value in state.get("discovered_ids", [])):
        return "NEWLY QUALIFIED"
    return "New"


def update_state(
    state: dict[str, Any],
    *,
    discovered_ids: Iterable[str],
    recommended_ids: Iterable[str],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    updated = _normalize_state(state)
    discovered = set(str(value) for value in updated["discovered_ids"])
    recommended = set(str(value) for value in updated["recommended_ids"])
    for tracking_id in (str(value) for value in discovered_ids):
        discovered.add(tracking_id)
        updated["last_seen_at"][tracking_id] = now
        updated["first_discovered_at"].setdefault(tracking_id, now)
    for tracking_id in (str(value) for value in recommended_ids):
        discovered.add(tracking_id)
        recommended.add(tracking_id)
        updated["last_seen_at"][tracking_id] = now
        updated["first_discovered_at"].setdefault(tracking_id, now)
        updated["first_recommended_at"].setdefault(tracking_id, now)
    updated["discovered_ids"] = sorted(discovered)
    updated["recommended_ids"] = sorted(recommended)
    updated["last_run"] = now
    return updated


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_normalize_state(state), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
