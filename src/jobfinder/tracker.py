from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Role,
    ScoredJob,
    normalize_application_url,
    normalize_company_name,
    normalize_job_title,
    normalize_location_name,
    similar_job_titles,
    stable_job_id,
)
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
INACTIVE_STATUSES = SUPPRESSED_STATUSES
REJECTION_REASONS = (
    "too pure SWE",
    "not AI/agentic enough",
    "too competitive",
    "bad location",
    "full-time only",
    "already applied",
    "not qualified",
    "other custom reason",
)
_STATUS_LOOKUP = {status.casefold(): status for status in STATUSES}
_STATUS_LOOKUP.update(
    {
        "dismissed": "Not Interested",
        "not interested": "Not Interested",
        "interested": "Saved",
        "saved / interested": "Saved",
    }
)


def normalize_status(value: str) -> str:
    status = _STATUS_LOOKUP.get(value.strip().casefold())
    if status is None:
        raise ValueError(f"Unsupported job status: {value}")
    return status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_data() -> dict[str, Any]:
    return {"version": 1, "jobs": {}}


def _identity(company: str, title: str, location: str, url: str) -> dict[str, str]:
    return {
        "normalized_company": normalize_company_name(company),
        "normalized_title": normalize_job_title(title),
        "normalized_location": normalize_location_name(location),
        "normalized_url": normalize_application_url(url),
    }


def _record_identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        **_identity(
            str(record.get("company", "")),
            str(record.get("title", "")),
            str(record.get("location", "")),
            str(record.get("url", "")),
        ),
        **{
            key: str(record.get(key) or "")
            for key in (
                "normalized_company",
                "normalized_title",
                "normalized_location",
                "normalized_url",
            )
            if record.get(key)
        },
    }


def _matches_role(record: dict[str, Any], job: Role) -> bool:
    role_identity = _identity(job.company, job.title, job.location, job.url)
    record_identity = _record_identity(record)
    same_company = (
        role_identity["normalized_company"]
        and role_identity["normalized_company"] == record_identity["normalized_company"]
    )
    same_url = (
        role_identity["normalized_url"]
        and role_identity["normalized_url"] == record_identity["normalized_url"]
    )
    same_location = (
        role_identity["normalized_location"]
        and role_identity["normalized_location"]
        == record_identity["normalized_location"]
    )
    same_title = (
        role_identity["normalized_title"]
        and role_identity["normalized_title"] == record_identity["normalized_title"]
    )
    similar_title = similar_job_titles(
        str(record.get("title", "")),
        job.title,
    )
    return bool(
        (same_company and same_url)
        or (same_company and same_location and (same_title or similar_title))
        or (same_url and (same_title or similar_title))
    )


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

    def suppression_match(
        self,
        job: Role,
        include_previous: bool = False,
    ) -> dict[str, Any] | None:
        """Return a suppressing tracked record matching a role exactly or likely."""
        with self._lock:
            records = self._load()["jobs"]
        exact = records.get(job.tracking_id)
        if isinstance(exact, dict):
            try:
                status = normalize_status(str(exact.get("status", "New")))
            except ValueError:
                status = "New"
            if (
                status in INACTIVE_STATUSES
                or exact.get("inactive") is True
                or include_previous
            ):
                return dict(exact)
        for record in records.values():
            if not isinstance(record, dict):
                continue
            try:
                status = normalize_status(str(record.get("status", "New")))
            except ValueError:
                status = "New"
            if (
                status not in INACTIVE_STATUSES
                and record.get("inactive") is not True
                and not include_previous
            ):
                continue
            if _matches_role(record, job):
                return dict(record)
        return None

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
                    "manual_reason": str(existing.get("manual_reason", "")),
                    "reason_category": str(existing.get("reason_category", "")),
                    "score": item.score.overall,
                    "competitiveness": item.score.competitiveness,
                    "employment_type": job.employment_type or "Not listed",
                    "application_url": job.url,
                    "source": job.source,
                    "source_url": job.url,
                    "recommendation_tier": bucket,
                    "why_recommended": "; ".join(item.score.why_match),
                    "ai_relevance": item.score.ai_focus,
                    "ai_engineer": item.score.ai_engineer,
                    "ai_classification_reason": item.score.ai_classification_reason,
                    "matched_keywords": list(item.score.ai_keywords),
                    "concerns": list(item.score.concerns),
                    "pure_swe_signal": item.score.pure_swe_signal,
                    **_identity(job.company, job.title, job.location, job.url),
                    "first_seen": str(existing.get("first_seen") or timestamp),
                    "last_seen": timestamp,
                    "updated_at": str(existing.get("updated_at") or timestamp),
                    "status_updated_at": str(
                        existing.get("status_updated_at")
                        or existing.get("updated_at")
                        or timestamp
                    ),
                }
            _save_tracker_data(self.path, data)

    def update_status(
        self,
        tracking_id: str,
        status: str,
        reason_category: str = "",
        manual_reason: str = "",
    ) -> dict[str, Any]:
        normalized = normalize_status(status)
        if reason_category and reason_category not in REJECTION_REASONS:
            reason_category = "other custom reason"
        with self._lock:
            data = self._load()
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record["status"] = normalized
            if normalized in {"Rejected", "Not Interested"}:
                record["reason_category"] = reason_category.strip()
                record["manual_reason"] = manual_reason.strip()[:1000]
            timestamp = _now()
            record["updated_at"] = timestamp
            record["status_updated_at"] = timestamp
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
                timestamp = _now()
                record["updated_at"] = timestamp
                record["status_updated_at"] = timestamp
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

    def update_reason(
        self,
        tracking_id: str,
        reason_category: str = "",
        manual_reason: str = "",
    ) -> dict[str, Any]:
        if reason_category and reason_category not in REJECTION_REASONS:
            reason_category = "other custom reason"
        if len(manual_reason) > 1000:
            raise ValueError("Reason must be 1000 characters or fewer")
        with self._lock:
            data = self._load()
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record["reason_category"] = reason_category.strip()
            record["manual_reason"] = manual_reason.strip()
            record["updated_at"] = _now()
            _save_tracker_data(self.path, data)
            return dict(record)

    def add_manual_job(
        self,
        *,
        company: str,
        title: str,
        location: str,
        url: str,
        status: str,
        reason_category: str = "",
        manual_reason: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        if not company.strip() or not title.strip() or not url.strip():
            raise ValueError("Company, title, and URL are required")
        normalized_status = normalize_status(status)
        if normalized_status in {"Rejected", "Not Interested"}:
            if reason_category and reason_category not in REJECTION_REASONS:
                reason_category = "other custom reason"
        tracking_id = stable_job_id(company, title, location, url)
        timestamp = _now()
        with self._lock:
            data = self._load()
            existing = data["jobs"].get(tracking_id, {})
            record = {
                "id": tracking_id,
                "company": company.strip(),
                "title": title.strip(),
                "location": location.strip() or "Location not listed",
                "url": url.strip(),
                "bucket": str(existing.get("bucket", "Manual")),
                "company_size": str(existing.get("company_size", "Unknown")),
                "role_family": str(existing.get("role_family", "Manual")),
                "status": normalized_status,
                "notes": notes.strip()[:5000],
                "manual_reason": manual_reason.strip()[:1000],
                "reason_category": reason_category.strip(),
                "score": existing.get("score", 0),
                "competitiveness": str(existing.get("competitiveness", "Unknown")),
                "employment_type": str(existing.get("employment_type", "Manual")),
                "application_url": url.strip(),
                "source": str(existing.get("source", "Manual entry")),
                "source_url": str(existing.get("source_url", url.strip())),
                "recommendation_tier": str(existing.get("recommendation_tier", "Manual")),
                "why_recommended": str(existing.get("why_recommended", "")),
                "ai_relevance": str(existing.get("ai_relevance", "")),
                "ai_engineer": bool(existing.get("ai_engineer", False)),
                "ai_classification_reason": str(
                    existing.get("ai_classification_reason", "")
                ),
                "matched_keywords": list(existing.get("matched_keywords", [])),
                "concerns": list(existing.get("concerns", [])),
                "pure_swe_signal": bool(existing.get("pure_swe_signal", False)),
                **_identity(company, title, location, url),
                "first_seen": str(existing.get("first_seen") or timestamp),
                "last_seen": str(existing.get("last_seen") or timestamp),
                "updated_at": timestamp,
                "status_updated_at": timestamp,
            }
            data["jobs"][tracking_id] = record
            _save_tracker_data(self.path, data)
            return dict(record)
