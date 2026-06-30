from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _location_path(city: dict[str, Any] | None) -> tuple[str, ...]:
    names: list[str] = []
    node = city
    while node:
        name = node.get("en_name") or node.get("i18n_name") or node.get("name")
        if name:
            names.append(str(name))
        node = node.get("parent")
    return tuple(names)


@dataclass(frozen=True)
class Job:
    id: str
    title: str
    description: str
    requirement: str
    city: str
    country: str
    location_path: tuple[str, ...]
    recruitment_type: str
    category: str
    subject: str
    expiry_time: int | None = None
    company: str = "ByteDance"

    @property
    def text(self) -> str:
        return " ".join(
            (
                self.title,
                self.description,
                self.requirement,
                self.category,
                self.subject,
            )
        ).lower()

    @property
    def location(self) -> str:
        if self.city and self.country and self.city != self.country:
            return f"{self.city}, {self.country}"
        return self.city or self.country or "Location not listed"

    @property
    def url(self) -> str:
        return f"https://jobs.bytedance.com/en/position/{self.id}/detail"

    @property
    def expires_at(self) -> datetime | None:
        if not self.expiry_time:
            return None
        value = self.expiry_time
        if value > 10_000_000_000:
            value //= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Job":
        city_info = raw.get("city_info") or {}
        path = _location_path(city_info)
        city = str(
            city_info.get("en_name")
            or city_info.get("i18n_name")
            or city_info.get("name")
            or ""
        )
        country = path[-1] if path else ""
        recruit = raw.get("recruit_type") or {}
        category = raw.get("job_category") or {}
        subject = raw.get("job_subject") or {}
        post_info = raw.get("job_post_info") or {}
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title") or "Untitled role"),
            description=str(raw.get("description") or ""),
            requirement=str(raw.get("requirement") or ""),
            city=city,
            country=country,
            location_path=path,
            recruitment_type=str(
                recruit.get("en_name")
                or recruit.get("i18n_name")
                or recruit.get("name")
                or ""
            ),
            category=str(
                category.get("en_name")
                or category.get("i18n_name")
                or category.get("name")
                or ""
            ),
            subject=str(
                subject.get("en_name")
                or subject.get("i18n_name")
                or subject.get("name")
                or ""
            ),
            expiry_time=post_info.get("expiry_time"),
        )


@dataclass(frozen=True)
class Score:
    skill_fit: float
    learning_value: float
    accessibility: float
    overall: float
    relevant: bool
    geography_ok: bool
    why_match: tuple[str, ...] = field(default_factory=tuple)
    concerns: tuple[str, ...] = field(default_factory=tuple)
    rejection_reason: str = ""
    competitiveness: str = "Medium"


@dataclass(frozen=True)
class ScoredJob:
    job: Job
    score: Score
    is_new: bool = False

