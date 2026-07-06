from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import ScoredJob
from .scoring import company_size_group


STATUSES = (
    "New",
    "Viewed",
    "Started",
    "Applied",
    "Rejected",
    "Saved",
    "Not Interested",
)
SUPPRESSED_STATUSES = frozenset({"Applied", "Rejected", "Not Interested"})
_STATUS_LOOKUP = {status.casefold(): status for status in STATUSES}


def normalize_status(value: str) -> str:
    status = _STATUS_LOOKUP.get(value.strip().casefold())
    if status is None:
        raise ValueError(f"Unsupported job status: {value}")
    return status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_data() -> dict[str, Any]:
    return {"version": 1, "jobs": {}}


def load_tracker_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_data()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_data()
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return _empty_data()
    data.setdefault("version", 1)
    return data


def _save_tracker_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class ApplicationTracker:
    """Persistent JSON-backed application status and notes store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        return load_tracker_data(self.path)

    def status_for(self, tracking_id: str) -> str:
        with self._lock:
            record = self._load()["jobs"].get(tracking_id, {})
        try:
            return normalize_status(str(record.get("status", "New")))
        except ValueError:
            return "New"

    def status_map(self) -> dict[str, str]:
        with self._lock:
            records = self._load()["jobs"]
        statuses: dict[str, str] = {}
        for tracking_id, record in records.items():
            if not isinstance(record, dict):
                continue
            try:
                statuses[str(tracking_id)] = normalize_status(
                    str(record.get("status", "New"))
                )
            except ValueError:
                statuses[str(tracking_id)] = "New"
        return statuses

    def get(self, tracking_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._load()["jobs"].get(tracking_id)
            return dict(record) if isinstance(record, dict) else None

    def list_jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        normalized = normalize_status(status) if status else None
        with self._lock:
            jobs = [
                dict(record)
                for record in self._load()["jobs"].values()
                if isinstance(record, dict)
            ]
        if normalized:
            jobs = [job for job in jobs if job.get("status") == normalized]
        return sorted(
            jobs,
            key=lambda job: (
                str(job.get("last_seen", "")),
                str(job.get("updated_at", "")),
            ),
            reverse=True,
        )

    def upsert_recommendations(
        self,
        recommendations: Iterable[tuple[ScoredJob, str]],
    ) -> None:
        timestamp = _now()
        with self._lock:
            data = self._load()
            jobs = data["jobs"]
            for item, bucket in recommendations:
                job = item.job
                tracking_id = job.tracking_id
                existing = jobs.get(tracking_id, {})
                try:
                    status = normalize_status(
                        str(existing.get("status", item.tracking_status))
                    )
                except ValueError:
                    status = "New"
                jobs[tracking_id] = {
                    "id": tracking_id,
                    "company": job.company,
                    "title": job.title,
                    "location": job.location,
                    "url": job.url,
                    "bucket": bucket,
                    "company_size": company_size_group(job),
                    "role_family": job.role_family or "Not classified",
                    "status": status,
                    "notes": str(existing.get("notes", "")),
                    "score": item.score.overall,
                    "competitiveness": item.score.competitiveness,
                    "first_seen": str(existing.get("first_seen") or timestamp),
                    "last_seen": timestamp,
                    "updated_at": str(existing.get("updated_at") or timestamp),
                }
            _save_tracker_data(self.path, data)

    def update_status(self, tracking_id: str, status: str) -> dict[str, Any]:
        normalized = normalize_status(status)
        with self._lock:
            data = self._load()
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record["status"] = normalized
            record["updated_at"] = _now()
            _save_tracker_data(self.path, data)
            return dict(record)

    def mark_viewed(self, tracking_id: str) -> dict[str, Any]:
        """Only advance New to Viewed; never infer Started or Applied."""
        with self._lock:
            data = self._load()
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            if record.get("status", "New") == "New":
                record["status"] = "Viewed"
                record["updated_at"] = _now()
                _save_tracker_data(self.path, data)
            return dict(record)

    def update_notes(self, tracking_id: str, notes: str) -> dict[str, Any]:
        if len(notes) > 5000:
            raise ValueError("Notes must be 5000 characters or fewer")
        with self._lock:
            data = self._load()
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record["notes"] = notes.strip()
            record["updated_at"] = _now()
            _save_tracker_data(self.path, data)
            return dict(record)
