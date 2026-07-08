from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _location_path(city: dict[str, Any] | None) -> tuple[str, ...]:
    names: list[str] = []
    node = city
    while node:
        name = node.get("en_name") or node.get("i18n_name") or node.get("name")
        if name:
            names.append(str(name))
        node = node.get("parent")
    return tuple(names)


def _infer_country(location: str) -> str:
    lowered = location.lower()
    if any(
        term in lowered
        for term in (
            "united states",
            " usa",
            "u.s.",
            "remote - us",
            "remote, us",
        )
    ):
        return "United States"
    if re.search(
        r",\s*(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|"
        r"Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
        r"Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
        r"Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|"
        r"New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
        r"Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|"
        r"Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|"
        r"District of Columbia)\b",
        location,
        re.IGNORECASE,
    ):
        return "United States"
    if re.search(
        r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|"
        r"MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|"
        r"SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
        location,
        re.IGNORECASE,
    ):
        return "United States"
    if re.search(
        r"\bUS\s+(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|"
        r"LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|"
        r"PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
        location,
        re.IGNORECASE,
    ):
        return "United States"
    if any(term in lowered for term in ("china", "beijing", "shanghai", "shenzhen")):
        return "China"
    return ""


def _start_period(title: str, description: str = "") -> str:
    text = f"{title} {description}".lower()
    year_match = re.search(r"\b(20\d{2})\b", text)
    season = next(
        (
            name.title()
            for name in ("winter", "spring", "summer", "fall")
            if name in text
        ),
        "",
    )
    year = year_match.group(1) if year_match else ""
    return " ".join(part for part in (season, year) if part) or "Flexible/unspecified"


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_PARAMS = {
    "gh_src",
    "gh_jid",
    "lever-source",
    "source",
    "src",
    "ref",
    "referrer",
    "campaign",
    "fbclid",
    "gclid",
    "msclkid",
}


def normalize_identity_text(value: str) -> str:
    """Normalize user-visible identity text for stable tracking/dedupe."""
    text = re.sub(r"&", " and ", value.casefold())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_company_name(value: str) -> str:
    text = normalize_identity_text(value)
    text = re.sub(
        r"\b(?:inc|incorporated|corp|corporation|llc|ltd|limited|co|company)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def normalize_job_title(value: str) -> str:
    text = normalize_identity_text(value)
    text = re.sub(
        r"\b(?:spring|summer|fall|autumn|winter|jan|jun|january|june)\b",
        " ",
        text,
    )
    text = re.sub(r"\b20\d{2}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_location_name(value: str) -> str:
    text = normalize_identity_text(value)
    replacements = {
        "united states of america": "united states",
        "u s a": "united states",
        "usa": "united states",
        "u s": "united states",
        "remote us": "remote united states",
        "remote u s": "remote united states",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return re.sub(r"\s+", " ", text).strip()


def normalize_application_url(url: str) -> str:
    """Normalize a job URL while preserving non-tracking query parameters."""
    parts = urlsplit(url.strip())
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.casefold() not in TRACKING_QUERY_PARAMS
        and not key.casefold().startswith(TRACKING_QUERY_PREFIXES)
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(kept_query, doseq=True),
            "",
        )
    )


def identity_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_job_title(value).split()
        if token
        not in {
            "intern",
            "internship",
            "co",
            "op",
            "student",
            "early",
            "career",
        }
    }


def similar_job_titles(first: str, second: str) -> bool:
    left = identity_tokens(first)
    right = identity_tokens(second)
    if not left or not right:
        return normalize_job_title(first) == normalize_job_title(second)
    overlap = len(left & right) / max(len(left), len(right))
    similarity = SequenceMatcher(
        None,
        normalize_job_title(first),
        normalize_job_title(second),
    ).ratio()
    return overlap >= 0.72 or similarity >= 0.84


def stable_job_id(
    company: str,
    title: str,
    location: str,
    application_url: str,
) -> str:
    """Create a stable identifier from the user-visible application identity."""
    identity = "\x1f".join(
        (
            normalize_company_name(company),
            normalize_job_title(title),
            normalize_location_name(location),
            normalize_application_url(application_url),
        )
    )
    return "job_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Role:
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
    external_url: str = ""
    source: str = "ByteDance Careers"
    posted_date: str | None = None
    role_family: str = ""
    company_size_category: str = "Big tech / famous lab"
    source_category: str = "Big tech / AI labs"

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
        return self.external_url or (
            f"https://jobs.bytedance.com/en/position/{self.id}/detail"
        )

    @property
    def employment_type(self) -> str:
        return self.recruitment_type

    @property
    def tracking_id(self) -> str:
        return stable_job_id(
            self.company,
            self.title,
            self.location,
            self.url,
        )

    @property
    def requirements(self) -> str:
        return self.requirement

    @property
    def start_year_or_season(self) -> str:
        return _start_period(self.title, self.description)

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

    @classmethod
    def normalized(
        cls,
        *,
        id: str,
        company: str,
        title: str,
        location: str,
        employment_type: str,
        url: str,
        source: str,
        description: str = "",
        requirements: str = "",
        posted_date: str | None = None,
        role_family: str = "",
        company_size_category: str = "Mid-size tech",
        source_category: str = "Mid-size tech",
    ) -> "Role":
        country = _infer_country(location)
        path = tuple(
            part.strip() for part in location.split(",") if part.strip()
        )
        if country and country.lower() not in {part.lower() for part in path}:
            path = (*path, country)
        return cls(
            id=id,
            title=title.strip() or "Untitled role",
            description=description.strip(),
            requirement=requirements.strip(),
            city=location.strip(),
            country=country,
            location_path=path,
            recruitment_type=employment_type.strip(),
            category=role_family.strip(),
            subject="",
            company=company,
            external_url=url,
            source=source,
            posted_date=posted_date,
            role_family=role_family,
            company_size_category=company_size_category,
            source_category=source_category,
        )


# Backward-compatible name used by the original ByteDance client.
Job = Role


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
    timing_fit: float = 5.0
    location_fit: float = 5.0
    career_value: float = 5.0
    bucket: str = "Target"
    internship_clarity: float = 0.0
    competition_ease: float = 5.0
    requirement_ease: float = 5.0
    us_stability: float = 5.0
    practical_value: float = 5.0
    popularity_penalty: float = 0.0
    ai_focus: str = "Adjacent"
    ai_keywords: tuple[str, ...] = field(default_factory=tuple)
    pure_swe_signal: bool = False
    ai_engineer: bool = False
    ai_classification_reason: str = ""
    ai_relevance_score: float = 0.0
    optimization_relevance_score: float = 0.0
    applied_math_relevance_score: float = 0.0
    data_relevance_score: float = 0.0
    quant_relevance_score: float = 0.0
    relevance_total: float = 0.0
    primary_track: str = ""


@dataclass(frozen=True)
class ScoredJob:
    job: Job
    score: Score
    is_new: bool = False
    tracking_status: str = "New"
