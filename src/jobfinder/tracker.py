from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
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


DISPLAY_STATUSES = (
    "New",
    "Viewed",
    "Saved",
    "Applied",
    "Rejected",
    "Not Interested",
)
STATUSES = DISPLAY_STATUSES
STORAGE_STATUS = {
    "New": "new",
    "Viewed": "viewed",
    "Saved": "saved",
    "Applied": "applied",
    "Rejected": "rejected",
    "Not Interested": "not_interested",
}
SUPPRESSED_STATUSES = frozenset({"Applied", "Rejected", "Not Interested"})
PERMANENT_BLOCK_STATUSES = SUPPRESSED_STATUSES
PREVIOUS_RECOMMENDATION_STATUSES = frozenset({"New", "Viewed"})
HISTORY_SUPPRESSION_DAYS = 365
INACTIVE_STATUSES = SUPPRESSED_STATUSES
REJECTION_REASONS = (
    "too SWE",
    "wrong location",
    "full-time only",
    "not AI focused",
    "already applied",
    "not qualified",
    "other",
    "too pure SWE",
    "not AI/agentic enough",
    "too competitive",
    "bad location",
)
_STATUS_LOOKUP = {status.casefold(): status for status in STATUSES}
_STATUS_LOOKUP.update({value: key for key, value in STORAGE_STATUS.items()})
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


def _empty_feedback() -> dict[str, Any]:
    return {"version": 1, "feedback": []}


def _display_record(record: dict[str, Any]) -> dict[str, Any]:
    copy = dict(record)
    copy["status"] = normalize_status(str(copy.get("status", "new")))
    if "job_id" in copy and "id" not in copy:
        copy["id"] = copy["job_id"]
    if "id" in copy and "job_id" not in copy:
        copy["job_id"] = copy["id"]
    if "reason" in copy and "manual_reason" not in copy:
        copy["manual_reason"] = str(copy.get("reason", ""))
    if "reason_category" in copy and "reason" not in copy:
        copy["reason"] = str(copy.get("reason_category", ""))
    return copy


def _storage_record(record: dict[str, Any]) -> dict[str, Any]:
    copy = _display_record(record)
    status = normalize_status(str(copy.get("status", "new")))
    timestamp = str(
        copy.get("timestamp")
        or copy.get("status_updated_at")
        or copy.get("updated_at")
        or _now()
    )
    reason = str(
        copy.get("reason")
        or copy.get("manual_reason")
        or copy.get("reason_category")
        or ""
    ).strip()
    copy["job_id"] = str(copy.get("job_id") or copy.get("id") or "")
    copy["id"] = copy["job_id"]
    copy["normalized_title"] = str(
        copy.get("normalized_title")
        or normalize_job_title(str(copy.get("title", "")))
    )
    copy["normalized_company"] = str(
        copy.get("normalized_company")
        or normalize_company_name(str(copy.get("company", "")))
    )
    copy["normalized_location"] = str(
        copy.get("normalized_location")
        or normalize_location_name(str(copy.get("location", "")))
    )
    copy["normalized_url"] = str(
        copy.get("normalized_url")
        or normalize_application_url(str(copy.get("url", "")))
    )
    copy["status"] = STORAGE_STATUS[status]
    copy["reason"] = reason
    copy["timestamp"] = timestamp
    copy["source"] = str(copy.get("source", "crawler"))
    return copy


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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


def load_feedback_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_feedback()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_feedback()
    if not isinstance(data, dict) or not isinstance(data.get("feedback"), list):
        return _empty_feedback()
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
        self.data_dir = path.parent
        self.history_path = self.data_dir / "job_history.json"
        self.feedback_path = self.data_dir / "user_feedback.json"
        self.manual_path = self.data_dir / "manual_jobs.json"
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        merged = _empty_data()
        jobs = merged["jobs"]
        for path in (
            self.history_path,
            self.manual_path,
            self.path,
        ):
            for tracking_id, record in load_tracker_data(path)["jobs"].items():
                if isinstance(record, dict):
                    display = _display_record(record)
                    display.setdefault("id", str(tracking_id))
                    display.setdefault("job_id", str(tracking_id))
                    jobs[str(display["id"])] = display
        return merged

    def _load_history(self) -> dict[str, Any]:
        return load_tracker_data(self.history_path)

    def _load_manual(self) -> dict[str, Any]:
        return load_tracker_data(self.manual_path)

    def _save_history(self, data: dict[str, Any]) -> None:
        _save_tracker_data(self.history_path, data)

    def _save_manual(self, data: dict[str, Any]) -> None:
        _save_tracker_data(self.manual_path, data)

    def _save_feedback(self, data: dict[str, Any]) -> None:
        _save_tracker_data(self.feedback_path, data)

    def _record_store(self, tracking_id: str) -> tuple[Path, dict[str, Any]]:
        manual = self._load_manual()
        if tracking_id in manual["jobs"]:
            return self.manual_path, manual
        history = self._load_history()
        if tracking_id in history["jobs"]:
            return self.history_path, history
        legacy = load_tracker_data(self.path)
        if tracking_id in legacy["jobs"]:
            history["jobs"][tracking_id] = legacy["jobs"][tracking_id]
            return self.history_path, history
        return self.history_path, history

    def _record_feedback(
        self,
        record: dict[str, Any],
        reason_category: str = "",
        manual_reason: str = "",
    ) -> None:
        status = normalize_status(str(record.get("status", "New")))
        if status not in {"Rejected", "Not Interested"}:
            return
        reason = (
            manual_reason
            or reason_category
            or str(record.get("reason", ""))
            or str(record.get("manual_reason", ""))
            or str(record.get("reason_category", ""))
        ).strip()
        if not reason:
            return
        data = load_feedback_data(self.feedback_path)
        event = {
            "job_id": str(record.get("id") or record.get("job_id") or ""),
            "company": str(record.get("company", "")),
            "title": str(record.get("title", "")),
            "reason": reason,
            "status": STORAGE_STATUS[status],
            "timestamp": str(record.get("timestamp") or _now()),
        }
        data["feedback"] = [
            item
            for item in data["feedback"]
            if not (
                isinstance(item, dict)
                and item.get("job_id") == event["job_id"]
                and item.get("status") == event["status"]
            )
        ]
        data["feedback"].append(event)
        self._save_feedback(data)

    def feedback_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        data = load_feedback_data(self.feedback_path)
        for item in data["feedback"]:
            if not isinstance(item, dict):
                continue
            reason = str(item.get("reason", "")).strip()
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
        for record in self._load()["jobs"].values():
            if not isinstance(record, dict):
                continue
            try:
                status = normalize_status(str(record.get("status", "New")))
            except ValueError:
                continue
            if status not in {"Rejected", "Not Interested"}:
                continue
            reason = str(
                record.get("reason")
                or record.get("manual_reason")
                or record.get("reason_category")
                or ""
            ).strip()
            if reason:
                counts.setdefault(reason, 0)
        return counts

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
            if self._should_suppress_record(exact, status, include_previous):
                return dict(exact)
        for record in records.values():
            if not isinstance(record, dict):
                continue
            try:
                status = normalize_status(str(record.get("status", "New")))
            except ValueError:
                status = "New"
            if not self._should_suppress_record(record, status, include_previous):
                continue
            if _matches_role(record, job):
                return dict(record)
        return None

    def _should_suppress_record(
        self,
        record: dict[str, Any],
        status: str,
        include_previous: bool,
    ) -> bool:
        if status in PERMANENT_BLOCK_STATUSES or record.get("inactive") is True:
            return True
        if not include_previous or status == "Saved":
            return False
        if status not in PREVIOUS_RECOMMENDATION_STATUSES:
            return False
        timestamp = _parse_time(
            record.get("last_seen")
            or record.get("timestamp")
            or record.get("updated_at")
            or record.get("first_seen")
        )
        if timestamp is None:
            return True
        return datetime.now(timezone.utc) - timestamp <= timedelta(
            days=HISTORY_SUPPRESSION_DAYS
        )

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
            data = self._load_history()
            jobs = data["jobs"]
            for item, bucket in recommendations:
                job = item.job
                tracking_id = job.tracking_id
                existing = self._load()["jobs"].get(tracking_id, {})
                try:
                    status = normalize_status(
                        str(existing.get("status", item.tracking_status))
                    )
                except ValueError:
                    status = "New"
                record = {
                    "id": tracking_id,
                    "job_id": tracking_id,
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
                    "source": "crawler",
                    "source_name": job.source,
                    "source_url": job.url,
                    "recommendation_tier": bucket,
                    "why_recommended": "; ".join(item.score.why_match),
                    "ai_relevance": item.score.ai_focus,
                    "ai_engineer": item.score.ai_engineer,
                    "ai_classification_reason": item.score.ai_classification_reason,
                    "matched_keywords": list(item.score.ai_keywords),
                    "concerns": list(item.score.concerns),
                    "pure_swe_signal": item.score.pure_swe_signal,
                    "primary_track": item.score.primary_track,
                    "relevance_total": item.score.relevance_total,
                    "ai_relevance_score": item.score.ai_relevance_score,
                    "optimization_relevance_score": item.score.optimization_relevance_score,
                    "applied_math_relevance_score": item.score.applied_math_relevance_score,
                    "data_relevance_score": item.score.data_relevance_score,
                    "quant_relevance_score": item.score.quant_relevance_score,
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
                jobs[tracking_id] = _storage_record(record)
            self._save_history(data)

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
            _path, data = self._record_store(tracking_id)
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record = _display_record(record)
            record["status"] = normalized
            if normalized in {"Rejected", "Not Interested"}:
                record["reason_category"] = reason_category.strip()
                record["manual_reason"] = manual_reason.strip()[:1000]
            timestamp = _now()
            record["updated_at"] = timestamp
            record["status_updated_at"] = timestamp
            record["timestamp"] = timestamp
            data["jobs"][tracking_id] = _storage_record(record)
            if _path == self.manual_path:
                self._save_manual(data)
            else:
                self._save_history(data)
            self._record_feedback(record, reason_category, manual_reason)
            return _display_record(record)

    def mark_viewed(self, tracking_id: str) -> dict[str, Any]:
        """Only advance New to Viewed; never infer Started or Applied."""
        with self._lock:
            _path, data = self._record_store(tracking_id)
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record = _display_record(record)
            if record.get("status", "New") == "New":
                record["status"] = "Viewed"
                timestamp = _now()
                record["updated_at"] = timestamp
                record["status_updated_at"] = timestamp
                record["timestamp"] = timestamp
                data["jobs"][tracking_id] = _storage_record(record)
                if _path == self.manual_path:
                    self._save_manual(data)
                else:
                    self._save_history(data)
            return _display_record(record)

    def update_notes(self, tracking_id: str, notes: str) -> dict[str, Any]:
        if len(notes) > 5000:
            raise ValueError("Notes must be 5000 characters or fewer")
        with self._lock:
            _path, data = self._record_store(tracking_id)
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record = _display_record(record)
            record["notes"] = notes.strip()
            record["updated_at"] = _now()
            data["jobs"][tracking_id] = _storage_record(record)
            if _path == self.manual_path:
                self._save_manual(data)
            else:
                self._save_history(data)
            return _display_record(record)

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
            _path, data = self._record_store(tracking_id)
            record = data["jobs"].get(tracking_id)
            if not isinstance(record, dict):
                raise KeyError(tracking_id)
            record = _display_record(record)
            record["reason_category"] = reason_category.strip()
            record["manual_reason"] = manual_reason.strip()
            record["updated_at"] = _now()
            record["reason"] = manual_reason.strip() or reason_category.strip()
            data["jobs"][tracking_id] = _storage_record(record)
            if _path == self.manual_path:
                self._save_manual(data)
            else:
                self._save_history(data)
            self._record_feedback(record, reason_category, manual_reason)
            return _display_record(record)

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
            data = self._load_manual()
            existing = data["jobs"].get(tracking_id, {})
            record = {
                "id": tracking_id,
                "job_id": tracking_id,
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
                "source": "manual",
                "source_name": str(existing.get("source_name", "Manual entry")),
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
                "primary_track": str(existing.get("primary_track", "")),
                "relevance_total": existing.get("relevance_total", 0),
                "ai_relevance_score": existing.get("ai_relevance_score", 0),
                "optimization_relevance_score": existing.get(
                    "optimization_relevance_score",
                    0,
                ),
                "applied_math_relevance_score": existing.get(
                    "applied_math_relevance_score",
                    0,
                ),
                "data_relevance_score": existing.get("data_relevance_score", 0),
                "quant_relevance_score": existing.get("quant_relevance_score", 0),
                **_identity(company, title, location, url),
                "first_seen": str(existing.get("first_seen") or timestamp),
                "last_seen": str(existing.get("last_seen") or timestamp),
                "updated_at": timestamp,
                "status_updated_at": timestamp,
                "timestamp": timestamp,
            }
            data["jobs"][tracking_id] = _storage_record(record)
            self._save_manual(data)
            self._record_feedback(record, reason_category, manual_reason)
            return _display_record(record)
