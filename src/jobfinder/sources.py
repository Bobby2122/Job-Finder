from __future__ import annotations

import html
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import (
    Role,
    normalize_application_url,
    normalize_company_name,
    normalize_job_title,
    normalize_location_name,
)
from .scoring import classify_ai_engineer, classify_career_relevance


class SourceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str = "",
        category: str = "parser_suspected_broken",
        suggested_fix: str = "Inspect the official careers page and update the adapter or parser.",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.category = category
        self.suggested_fix = suggested_fix


class SourceUnavailable(SourceError):
    pass


SUCCESS_STATUSES = {"success", "success_no_jobs", "partial_success", "stale_cache"}
FAILURE_STATUSES = {"source_failure", "configuration_error"}
SOURCE_CACHE_VERSION = 1
CACHE_TTL_DAYS = 7

TEMPORARY_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class SourceResult:
    company: str
    source_type: str
    status: str
    jobs: list[Role] = field(default_factory=list)
    error_message: str | None = None
    fetched_at: datetime | None = None
    endpoint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class JobSource(Protocol):
    def fetch_jobs(self, company: dict[str, Any]) -> SourceResult:
        ...


def _source_category_for_http(code: int) -> tuple[str, str]:
    if code == 429:
        return "rate_limited", "Retry later with bounded backoff or reduce request volume."
    if code == 404:
        return "invalid_configuration", "Verify the ATS provider and board slug against the official careers page."
    if code == 403:
        return "blocked", "Use an official API endpoint where available or classify the official page as unsupported."
    if code == 422:
        return "invalid_configuration", "Verify request body, tenant, site, and pagination parameters."
    if code in {301, 302, 307, 308}:
        return "invalid_configuration", "Replace the endpoint with the current canonical official careers URL."
    if 500 <= code < 600:
        return "rate_limited", "Retry later; provider returned a server-side error."
    return "invalid_configuration", "Inspect provider response and update the adapter."


def _retry_delay(exc: HTTPError | None, attempt: int) -> float:
    retry_after = ""
    if exc is not None:
        retry_after = str(exc.headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return min(float(retry_after), 8.0)
    return min(8.0, 0.4 * (2**attempt) + random.uniform(0.0, 0.2))


def _sanitize_error_message(message: str, limit: int = 220) -> str:
    text = re.sub(r"(token|key|secret|signature|password)=([^&\s]+)", r"\1=<redacted>", str(message), flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def detect_ats_from_url(url: str) -> dict[str, str]:
    text = url.strip()
    lowered = text.lower()
    detected: dict[str, str] = {}
    if "boards.greenhouse.io/" in lowered or "job-boards.greenhouse.io/" in lowered:
        token = re.search(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", text, re.I)
        detected["type"] = "greenhouse"
        if token:
            detected["board_token"] = token.group(1)
    elif "jobs.lever.co/" in lowered:
        token = re.search(r"jobs\.lever\.co/([^/?#]+)", text, re.I)
        detected["type"] = "lever"
        if token:
            detected["site_token"] = token.group(1)
    elif "jobs.ashbyhq.com/" in lowered:
        token = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", text, re.I)
        detected["type"] = "ashby"
        if token:
            detected["board_name"] = token.group(1)
    elif "myworkdayjobs.com" in lowered:
        match = re.search(
            r"https?://([^/]+)/(?:(?:[^/]+)/)?([^/?#]+)(?:/)?",
            text,
            re.I,
        )
        detected["type"] = "workday"
        if match:
            detected["workday_host"] = match.group(1)
            path_site = match.group(2)
            detected["site"] = path_site
            detected["tenant"] = match.group(1).split(".", 1)[0]
    return detected


def _canonical_source_config(source: dict[str, Any]) -> dict[str, Any]:
    config = dict(source)
    if config.get("name") and not config.get("company"):
        config["company"] = config["name"]
    nested = config.get("source")
    if isinstance(nested, dict):
        source_type = str(nested.get("type") or config.get("adapter") or "")
        config.setdefault("adapter", source_type)
        config.setdefault("ats_type", source_type)
        for target, aliases in {
            "board_slug": ("board_token", "site_token", "board_name", "token"),
            "identifier": ("board_token", "site_token", "board_name", "token"),
            "workday_host": ("workday_host",),
            "tenant": ("tenant",),
            "site": ("site",),
        }.items():
            for alias in aliases:
                if nested.get(alias) and not config.get(target):
                    config[target] = nested[alias]
                    break
    if not config.get("adapter") and config.get("ats_type"):
        config["adapter"] = config["ats_type"]
    if not config.get("ats_type") and config.get("adapter"):
        config["ats_type"] = config["adapter"]
    url = str(config.get("careers_url") or config.get("careers_page") or config.get("endpoint") or "")
    detected = detect_ats_from_url(url)
    if detected and not config.get("adapter"):
        config["adapter"] = detected["type"]
        config["ats_type"] = detected["type"]
    if detected.get("type") and not config.get("detected_ats_type"):
        config["detected_ats_type"] = detected["type"]
    for source_key in ("board_token", "site_token", "board_name"):
        if detected.get(source_key) and not config.get("board_slug"):
            config["board_slug"] = detected[source_key]
            config["identifier"] = detected[source_key]
    if detected.get("type") == "workday":
        for key in ("workday_host", "tenant", "site"):
            if detected.get(key) and not config.get(key):
                config[key] = detected[key]
    adapter = str(config.get("adapter") or "")
    if adapter == "greenhouse" and not config.get("endpoint") and config.get("board_slug"):
        config["endpoint"] = f"https://boards-api.greenhouse.io/v1/boards/{config['board_slug']}/jobs?content=true"
    elif adapter == "lever" and not config.get("endpoint") and config.get("board_slug"):
        region = "api.eu.lever.co" if config.get("region") == "eu" else "api.lever.co"
        config["endpoint"] = f"https://{region}/v0/postings/{config['board_slug']}?mode=json"
    elif adapter == "ashby" and not config.get("endpoint") and config.get("board_slug"):
        config["endpoint"] = f"https://api.ashbyhq.com/posting-api/job-board/{config['board_slug']}"
    elif adapter == "workday" and not config.get("endpoint"):
        host = config.get("workday_host")
        tenant = config.get("tenant")
        site = config.get("site")
        if host and tenant and site:
            config["endpoint"] = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    return config


def validate_source_config(source: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not source.get("company"):
        problems.append("missing company")
    adapter = str(source.get("adapter") or source.get("ats_type") or "")
    if not adapter:
        problems.append("missing adapter/source type")
        return problems
    if adapter not in ADAPTERS:
        problems.append(f"unsupported adapter {adapter}")
        return problems
    if adapter in {"greenhouse", "lever", "ashby"} and not source.get("board_slug"):
        problems.append(f"{adapter} source requires board_slug/token")
    if adapter == "workday":
        if not source.get("endpoint") and not (
            source.get("workday_host") and source.get("tenant") and source.get("site")
        ):
            problems.append("workday source requires endpoint or host/tenant/site")
    if adapter == "careers_page" and not (
        source.get("careers_page") or source.get("endpoint")
    ):
        problems.append("careers_page source requires careers_page or endpoint")
    return problems


def _plain_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<(?:br|/p|/li|/div|/h\d)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _role_family(title: str, text: str = "") -> str:
    value = f"{title} {text}".lower()
    probe = Role.normalized(
        id="role-family-probe",
        company="",
        title=title,
        location="",
        employment_type="Internship",
        url="",
        source="",
        description=text,
        requirements=text,
    )
    if classify_ai_engineer(probe).is_ai_engineer:
        return "AI Engineer / Agentic AI"
    relevance = classify_career_relevance(probe)
    if relevance.primary_track == "Operations Research / Optimization":
        return "Optimization / OR"
    if relevance.primary_track == "Applied Math / Computational Math":
        return "Applied Math / Scientific Computing"
    if relevance.primary_track == "Data Science / Statistics":
        return "Data Science"
    if relevance.primary_track == "Quant / Risk Modeling":
        return "Quant / Risk"
    families = (
        ("Machine Learning / AI", ("machine learning", "artificial intelligence", " ai ", "ai platform", "ml engineer", "llm", "rag", "embeddings")),
        ("Data Science", ("data scientist", "data science", "decision scientist")),
        ("Quant / Risk", ("quant", "risk", "actuari", "market data")),
        ("Optimization / OR", ("operations research", "optimization", "supply chain", "forecast")),
        ("Research", ("research", "scientist")),
        ("Analytics", ("analytics", "analyst", "business intelligence")),
        ("Data Infrastructure", ("data engineer", "data platform", "ml platform")),
    )
    for family, terms in families:
        if any(term in value for term in terms):
            return family
    return "Other"


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 12.0,
    retries: int = 1,
) -> dict[str, Any] | list[Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "BobbyOpportunityTracker/0.2",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            last_error = exc
            if exc.code in TEMPORARY_HTTP_STATUSES and attempt < retries:
                time.sleep(_retry_delay(exc, attempt))
                continue
            break
        except (
            URLError,
            TimeoutError,
            IncompleteRead,
            RemoteDisconnected,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(_retry_delay(None, attempt))
    if isinstance(last_error, HTTPError):
        category, suggested_fix = _source_category_for_http(last_error.code)
        raise SourceUnavailable(
            f"{url}: HTTP {last_error.code}",
            error_code=f"HTTP {last_error.code}",
            category=category,
            suggested_fix=suggested_fix,
        )
    category = "partial_results" if isinstance(
        last_error,
        (URLError, TimeoutError, IncompleteRead, RemoteDisconnected),
    ) else "parser_suspected_broken"
    raise SourceError(
        f"{url}: {type(last_error).__name__}",
        error_code=type(last_error).__name__ if last_error else "Unknown",
        category=category,
        suggested_fix="Retry later if this is transient; otherwise inspect the official careers page.",
    )


def _request_text(
    url: str,
    *,
    timeout: float = 15.0,
    retries: int = 1,
    accept: str = "text/html,application/xhtml+xml",
) -> str:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": "BobbyOpportunityTracker/0.2",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read(2_500_000).decode("utf-8", errors="replace")
        except HTTPError as exc:
            last_error = exc
            if exc.code in TEMPORARY_HTTP_STATUSES and attempt < retries:
                time.sleep(_retry_delay(exc, attempt))
                continue
            break
        except (URLError, TimeoutError, IncompleteRead, RemoteDisconnected) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(_retry_delay(None, attempt))
    if isinstance(last_error, HTTPError):
        category, suggested_fix = _source_category_for_http(last_error.code)
        raise SourceUnavailable(
            f"{url}: HTTP {last_error.code}",
            error_code=f"HTTP {last_error.code}",
            category=category,
            suggested_fix=suggested_fix,
        )
    raise SourceError(
        f"{url}: {type(last_error).__name__}",
        error_code=type(last_error).__name__ if last_error else "Unknown",
        category="partial_results",
        suggested_fix="Retry later or replace with a stable official endpoint if available.",
    )


class SourceAdapter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def company(self) -> str:
        return str(self.config["company"])

    @property
    def provider(self) -> str:
        return str(self.config.get("ats_type") or self.config.get("adapter") or "unknown")

    @property
    def board_slug(self) -> str:
        return str(self.config.get("board_slug") or self.config.get("identifier") or "")

    @property
    def endpoint(self) -> str:
        if self.config.get("endpoint"):
            return str(self.config["endpoint"])
        if self.config.get("careers_page"):
            return str(self.config["careers_page"])
        return ""

    @property
    def source_type(self) -> str:
        return str(self.config.get("ats_type") or self.config.get("adapter") or "unknown")

    def _role(self, **values: Any) -> Role:
        now = datetime.now(timezone.utc).isoformat()
        location = str(values.get("location") or "")
        employment_type = str(values.get("employment_type") or "")
        source_job_id = str(values.pop("source_job_id", "") or values.get("id") or "")
        return Role.normalized(
            company=self.company,
            company_size_category=str(
                self.config.get("company_size_category", "Mid-size tech")
            ),
            source_category=str(
                self.config.get("source_category", "Mid-size tech")
            ),
            source_type=self.source_type,
            source_company_key=self.board_slug,
            source_job_id=source_job_id,
            source_endpoint=self.endpoint,
            source_url=self.endpoint,
            fetched_at=now,
            last_verified_at=now,
            raw_location=str(values.pop("raw_location", location)),
            raw_employment_type=str(
                values.pop("raw_employment_type", employment_type)
            ),
            **values,
        )

    def fetch(self, keywords: list[str]) -> list[Role]:
        raise NotImplementedError

    def fetch_result(self, keywords: list[str]) -> SourceResult:
        fetched_at = datetime.now(timezone.utc)
        started = time.monotonic()
        roles = self.fetch(keywords)
        status = "success" if roles else "success_no_jobs"
        return SourceResult(
            company=self.company,
            source_type=self.source_type,
            status=status,
            jobs=roles,
            fetched_at=fetched_at,
            endpoint=self.endpoint,
            metadata={
                "duration_seconds": round(time.monotonic() - started, 3),
                "attempt_count": int(self.config.get("retries", 1)) + 1,
                "source_company_key": self.board_slug,
            },
        )


class ByteDanceAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        from .client import ByteDanceClient

        client = ByteDanceClient(
            timeout=float(self.config.get("timeout", 20)),
            retries=int(self.config.get("retries", 1)),
        )
        source_keywords = keywords[: int(self.config.get("keyword_limit", 16))]
        roles = client.search(
            source_keywords,
            int(self.config.get("page_size", 20)),
            int(self.config.get("max_pages_per_keyword", 2)),
        )
        return [
            Role(
                **{
                    **role.__dict__,
                    "company": self.company,
                    "source": "ByteDance official careers API",
                    "company_size_category": str(
                        self.config.get(
                            "company_size_category",
                            "Big tech / famous lab",
                        )
                    ),
                    "source_category": str(
                        self.config.get(
                            "source_category",
                            "Big tech / AI labs",
                        )
                    ),
                    "role_family": _role_family(role.title, role.text),
                }
            )
            for role in roles
        ]


class GreenhouseAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        token = self.board_slug
        url = self.endpoint or f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        data = _request_json(
            url,
            timeout=float(self.config.get("timeout", 12)),
            retries=int(self.config.get("retries", 1)),
        )
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        roles: list[Role] = []
        for raw in jobs:
            content = _plain_text(raw.get("content"))
            location = str((raw.get("location") or {}).get("name") or "")
            office_locations = [
                str(item.get("location") or "")
                for item in raw.get("offices", [])
                if item.get("location")
            ]
            if office_locations and not re.search(
                r"\b(?:USA|United States|Remote)|,\s*[A-Z]{2}\b",
                location,
                re.IGNORECASE,
            ):
                location = office_locations[0]
            departments = ", ".join(
                str(item.get("name") or "") for item in raw.get("departments", [])
            )
            title = str(raw.get("title") or "")
            job_id = str(raw.get("id") or "")
            roles.append(
                self._role(
                    id=f"greenhouse:{token}:{job_id or normalize_job_title(title)}",
                    source_job_id=job_id,
                    title=title,
                    location=location,
                    employment_type=_employment_type(title, ""),
                    url=str(raw.get("absolute_url") or ""),
                    source="Greenhouse official job board API",
                    description=content,
                    requirements=content,
                    posted_date=raw.get("updated_at"),
                    role_family=_role_family(str(raw.get("title") or ""), departments),
                )
            )
        return roles


class LeverAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        site = self.board_slug
        region = "api.eu.lever.co" if self.config.get("region") == "eu" else "api.lever.co"
        url = self.endpoint or f"https://{region}/v0/postings/{site}?mode=json"
        data = _request_json(
            url,
            timeout=float(self.config.get("timeout", 12)),
            retries=int(self.config.get("retries", 1)),
        )
        jobs = data if isinstance(data, list) else []
        roles: list[Role] = []
        for raw in jobs:
            categories = raw.get("categories") or {}
            description = " ".join(
                part
                for part in (
                    _plain_text(raw.get("descriptionPlain")),
                    _plain_text(raw.get("additionalPlain")),
                    " ".join(
                        _plain_text(item.get("content"))
                        for item in raw.get("lists", [])
                    ),
                )
                if part
            )
            title = str(raw.get("text") or "")
            posting_id = str(raw.get("id") or "")
            roles.append(
                self._role(
                    id=f"lever:{site}:{posting_id or normalize_job_title(title)}",
                    source_job_id=posting_id,
                    title=title,
                    location=str(categories.get("location") or ""),
                    employment_type=str(categories.get("commitment") or ""),
                    url=str(raw.get("hostedUrl") or raw.get("applyUrl") or ""),
                    source="Lever official postings API",
                    description=description,
                    requirements=description,
                    posted_date=str(raw.get("createdAt") or "") or None,
                    role_family=_role_family(
                        title,
                        f"{categories.get('team', '')} {categories.get('department', '')}",
                    ),
                )
            )
        return roles


class AshbyAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        board = self.board_slug
        url = self.endpoint or f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        data = _request_json(
            url,
            timeout=float(self.config.get("timeout", 12)),
            retries=int(self.config.get("retries", 1)),
        )
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        roles: list[Role] = []
        for raw in jobs:
            if raw.get("isListed") is False:
                continue
            title = str(raw.get("title") or "")
            job_id = str(raw.get("id") or raw.get("jobId") or raw.get("jobUrl") or raw.get("applyUrl") or "")
            description = _plain_text(
                raw.get("descriptionPlain") or raw.get("descriptionHtml")
            )
            roles.append(
                self._role(
                    id=f"ashby:{board}:{job_id or normalize_job_title(title)}",
                    source_job_id=job_id,
                    title=title,
                    location=str(raw.get("location") or ""),
                    employment_type=str(raw.get("employmentType") or ""),
                    url=str(raw.get("jobUrl") or raw.get("applyUrl") or ""),
                    source="Ashby official job postings API",
                    description=description,
                    requirements=description,
                    posted_date=raw.get("publishedAt"),
                    role_family=_role_family(
                        title,
                        f"{raw.get('department', '')} {raw.get('team', '')}",
                    ),
                )
            )
        return roles


class WorkdayAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        self._detail_failures = 0
        endpoint = str(self.config["endpoint"]).rstrip("/")
        site_root = endpoint.removesuffix("/jobs")
        raw_found: dict[str, dict[str, Any]] = {}
        source_keywords = [
            str(value) for value in self.config.get("keywords", keywords)
        ]
        limit = int(self.config.get("page_size", 20))
        max_pages = int(self.config.get("max_pages_per_keyword", 3))
        for keyword in source_keywords[: int(self.config.get("keyword_limit", 8))]:
            for page in range(max_pages):
                offset = page * limit
                data = _request_json(
                    endpoint,
                    method="POST",
                    payload={
                        "appliedFacets": dict(self.config.get("applied_facets", {})),
                        "limit": limit,
                        "offset": offset,
                        "searchText": keyword,
                    },
                    timeout=float(self.config.get("timeout", 18)),
                    retries=int(self.config.get("retries", 1)),
                )
                postings = data.get("jobPostings", []) if isinstance(data, dict) else []
                for raw in postings:
                    external_path = str(raw.get("externalPath") or "")
                    raw_found[external_path or str(raw.get("title") or "")] = raw
                total = int(data.get("total", len(postings))) if isinstance(data, dict) else len(postings)
                if not postings or offset + limit >= total:
                    break

        detail_terms = (
            "intern",
            "co-op",
            "student",
            "fixed term",
            "analyst",
            "associate",
            "actuarial",
            "data scientist",
            "research assistant",
            "engineer i",
            "ai engineer",
            "llm",
            "agent",
            "automation",
            "generative ai",
        )
        detail_candidates = [
            raw
            for raw in raw_found.values()
            if any(
                term in str(raw.get("title") or "").lower()
                for term in detail_terms
            )
        ][: int(self.config.get("detail_limit", 35))]
        details: dict[str, dict[str, Any]] = {}

        def fetch_detail(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            external_path = str(raw.get("externalPath") or "")
            if not external_path:
                return "", {}
            try:
                response = _request_json(
                    f"{site_root}{external_path}",
                    timeout=10,
                    retries=0,
                )
                info = (
                    response.get("jobPostingInfo") or {}
                    if isinstance(response, dict)
                    else {}
                )
                return external_path, info
            except SourceError:
                self._detail_failures += 1
                return external_path, {}

        with ThreadPoolExecutor(max_workers=6) as executor:
            for external_path, detail in executor.map(
                fetch_detail,
                detail_candidates,
            ):
                if external_path and detail:
                    details[external_path] = detail

        found: dict[str, Role] = {}
        for raw in raw_found.values():
            external_path = str(raw.get("externalPath") or "")
            detail = details.get(external_path, {})
            title = str(detail.get("title") or raw.get("title") or "")
            location = str(
                detail.get("location") or raw.get("locationsText") or ""
            )
            description = _plain_text(
                detail.get("jobDescription")
                or " ".join(
                    str(value) for value in raw.get("bulletFields", []) if value
                )
            )
            role = self._role(
                id=f"workday:{self.company}:{external_path or title}",
                source_job_id=f"workday:{self.company}:{external_path or title}",
                title=title,
                location=location,
                employment_type=str(
                    detail.get("timeType") or _employment_type(title, description)
                ),
                url=str(detail.get("externalUrl") or f"{site_root}{external_path}"),
                source="Workday official careers API",
                description=description,
                requirements=description,
                posted_date=detail.get("postedOn") or raw.get("postedOn"),
                role_family=_role_family(title, description),
            )
            found[role.id] = role
        return list(found.values())

    def fetch_result(self, keywords: list[str]) -> SourceResult:
        result = super().fetch_result(keywords)
        failures = int(getattr(self, "_detail_failures", 0))
        if failures:
            metadata = dict(result.metadata)
            metadata["detail_failures"] = failures
            return SourceResult(
                company=result.company,
                source_type=result.source_type,
                status="partial_success",
                jobs=result.jobs,
                error_message=f"{failures} Workday detail request(s) failed",
                fetched_at=result.fetched_at,
                endpoint=result.endpoint,
                metadata=metadata,
            )
        return result


class CareersPageAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        url = str(self.config.get("careers_page") or self.config.get("endpoint") or "")
        if not url:
            raise SourceUnavailable(
                "careers_page adapter requires careers_page or endpoint",
                error_code="CONFIG",
                category="invalid_configuration",
                suggested_fix="Add the company's official careers page URL.",
            )
        try:
            html_text = _request_text(
                url,
                timeout=float(self.config.get("timeout", 15)),
                retries=int(self.config.get("retries", 1)),
            )
        except SourceError:
            raise

        roles: list[Role] = []
        for match in re.finditer(
            r"<script[^>]+type=[\"']application/ld\\+json[\"'][^>]*>(.*?)</script>",
            html_text,
            flags=re.I | re.S,
        ):
            try:
                payload = json.loads(html.unescape(match.group(1)).strip())
            except json.JSONDecodeError:
                continue
            entries = payload if isinstance(payload, list) else [payload]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                graph = entry.get("@graph")
                if isinstance(graph, list):
                    entries.extend(item for item in graph if isinstance(item, dict))
                    continue
                if entry.get("@type") != "JobPosting":
                    continue
                title = str(entry.get("title") or "")
                location_data = entry.get("jobLocation") or {}
                if isinstance(location_data, list):
                    location_data = location_data[0] if location_data else {}
                address = location_data.get("address") if isinstance(location_data, dict) else {}
                location = ", ".join(
                    str(value)
                    for value in (
                        address.get("addressLocality") if isinstance(address, dict) else "",
                        address.get("addressRegion") if isinstance(address, dict) else "",
                        address.get("addressCountry") if isinstance(address, dict) else "",
                    )
                    if value
                )
                apply_url = str(entry.get("url") or url)
                description = _plain_text(str(entry.get("description") or ""))
                roles.append(
                    self._role(
                        id=f"careers_page:{self.company}:{apply_url or title}",
                        title=title,
                        location=location or "Location not listed",
                        employment_type=str(entry.get("employmentType") or _employment_type(title, description)),
                        url=apply_url,
                        source="Official careers page structured data",
                        description=description,
                        requirements=description,
                        posted_date=entry.get("datePosted"),
                        role_family=_role_family(title, description),
                    )
                )
        if roles:
            return roles

        raise SourceError(
            f"{url}: no JobPosting structured data found",
            error_code="NO_STRUCTURED_JOBS",
            category="parser_suspected_broken",
            suggested_fix="Add a dedicated adapter for this official careers page or replace it with a stable ATS API.",
        )


class GoogleCareersAdapter(SourceAdapter):
    DEFAULT_URL = (
        "https://www.google.com/about/careers/applications/jobs/results/"
        "?q=intern&location=United%20States"
    )

    def fetch(self, keywords: list[str]) -> list[Role]:
        url = str(self.config.get("endpoint") or self.config.get("careers_page") or self.DEFAULT_URL)
        html_text = _request_text(
            url,
            timeout=float(self.config.get("timeout", 20)),
            retries=int(self.config.get("retries", 1)),
        )
        roles = self._parse_html(html_text, url)
        if not roles:
            raise SourceError(
                f"{url}: Google careers structured payload was not found",
                error_code="NO_GOOGLE_PAYLOAD",
                category="parser_suspected_broken",
                suggested_fix="Re-check the official Google careers page; the app payload shape may have changed.",
            )
        return roles

    def _parse_html(self, html_text: str, source_url: str) -> list[Role]:
        match = re.search(
            r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(.*?), sideChannel",
            html_text,
            flags=re.S,
        )
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise SourceError(
                f"{source_url}: invalid Google careers payload",
                error_code="INVALID_GOOGLE_PAYLOAD",
                category="parser_suspected_broken",
                suggested_fix="Update the Google careers adapter for the new structured payload shape.",
            ) from exc
        raw_jobs = payload[0] if payload and isinstance(payload[0], list) else []
        roles: list[Role] = []
        for raw in raw_jobs:
            if not isinstance(raw, list) or len(raw) < 10:
                continue
            job_id = str(raw[0] or "")
            title = str(raw[1] or "")
            apply_url = str(raw[2] or "")
            description = " ".join(
                _plain_text(block[1])
                for block in (raw[3], raw[4], raw[10] if len(raw) > 10 else None, raw[15] if len(raw) > 15 else None)
                if isinstance(block, list) and len(block) > 1 and block[1]
            )
            locations = raw[9] if isinstance(raw[9], list) else []
            us_locations = [
                str(item[0])
                for item in locations
                if isinstance(item, list)
                and item
                and (len(item) < 6 or str(item[5]).upper() == "US")
            ]
            location = "; ".join(us_locations[:6]) or "United States"
            classification = "Intern & Apprentice" if "intern" in title.lower() or "student" in title.lower() else ""
            detail_match = re.search(
                rf'href="([^"]*{re.escape(job_id)}[^"]*)"',
                html_text,
            )
            if detail_match:
                detail_url = html.unescape(detail_match.group(1))
                if detail_url.startswith("jobs/"):
                    apply_url = "https://www.google.com/about/careers/applications/" + detail_url
            roles.append(
                self._role(
                    id=f"google:{job_id or normalize_job_title(title)}",
                    title=title,
                    location=location,
                    employment_type=classification or _employment_type(title, description),
                    url=apply_url,
                    source="Google official careers structured page",
                    description=description,
                    requirements=description,
                    posted_date=None,
                    role_family=_role_family(title, description),
                )
            )
        return roles


class AppleCareersAdapter(SourceAdapter):
    DEFAULT_URL = (
        "https://jobs.apple.com/en-us/search?"
        "search=intern&location=united-states-USA"
    )

    def fetch(self, keywords: list[str]) -> list[Role]:
        base_url = str(self.config.get("endpoint") or self.config.get("careers_page") or self.DEFAULT_URL)
        max_pages = int(self.config.get("max_pages", 2))
        roles: dict[str, Role] = {}
        for page in range(1, max_pages + 1):
            url = base_url if page == 1 else f"{base_url}&page={page}"
            html_text = _request_text(
                url,
                timeout=float(self.config.get("timeout", 20)),
                retries=int(self.config.get("retries", 1)),
            )
            parsed = self._parse_html(html_text, url)
            for role in parsed:
                roles[role.id] = role
            if f"page={page + 1}" not in html_text:
                break
        if not roles:
            if "search-result-set" in html_text or "search-result-count" in html_text:
                return []
            raise SourceError(
                f"{base_url}: no Apple careers roles parsed",
                error_code="NO_APPLE_ROLES",
                category="official_page_unstructured",
                suggested_fix="Re-check the official Apple careers page; the server-rendered search markup may have changed.",
            )
        return list(roles.values())

    def _parse_html(self, html_text: str, source_url: str) -> list[Role]:
        roles: list[Role] = []
        chunks = re.split(r"<li data-core-accordion-item", html_text)
        for chunk in chunks[1:]:
            id_match = re.search(r"PIPE-(\d+)|role-search-job-title-[A-Z]*-(\d+)", chunk)
            title_match = re.search(
                r'aria-label="Role description:\s*([^"]+)"',
                chunk,
            )
            link_match = re.search(
                r'href="(/en-us/details/(\d+)/[^"]+)"',
                chunk,
            )
            if not title_match or not link_match:
                continue
            job_id = next((group for group in id_match.groups() if group), "") if id_match else link_match.group(2)
            title = html.unescape(title_match.group(1)).strip()
            if not re.search(r"\b(intern|internship|student|co-op|coop)\b", title, re.I):
                continue
            url = "https://jobs.apple.com" + html.unescape(link_match.group(1))
            description_match = re.search(
                r"job-summary-\d+.*?<span>(.*?)</span>",
                chunk,
                flags=re.S,
            )
            description = _plain_text(description_match.group(1) if description_match else "")
            role_number = job_id or normalize_job_title(title)
            location = "United States"
            location_match = re.search(
                r"Where we&#x27;re hiring[^>]+href=\"[^\"]+/locationPicker\"",
                chunk,
            )
            if location_match:
                location = "United States"
            roles.append(
                self._role(
                    id=f"apple:{role_number}",
                    title=title,
                    location=location,
                    employment_type=_employment_type(title, description),
                    url=url,
                    source="Apple official careers search page",
                    description=description,
                    requirements=description,
                    posted_date=None,
                    role_family=_role_family(title, description),
                )
            )
        return roles


class SmartRecruitersAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        company_id = self.config["identifier"]
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
            "?limit=100"
        )
        data = _request_json(
            url,
            timeout=float(self.config.get("timeout", 12)),
            retries=int(self.config.get("retries", 1)),
        )
        postings = data.get("content", []) if isinstance(data, dict) else []
        detail_terms = (
            "intern",
            "co-op",
            "student",
            "data",
            "analytics",
            "scientist",
            "machine learning",
            "ai",
            "optimization",
            "simulation",
            "model",
            "quant",
            "risk",
            "actuarial",
        )
        detail_limit = int(self.config.get("detail_limit", 35))
        detail_candidates = [
            raw
            for raw in postings
            if any(term in str(raw.get("name") or "").lower() for term in detail_terms)
        ][:detail_limit]
        details: dict[str, dict[str, Any]] = {}

        def fetch_detail(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            posting_id = str(raw.get("id") or raw.get("uuid") or "")
            if not posting_id:
                return "", {}
            try:
                detail = _request_json(
                    f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{posting_id}",
                    timeout=10,
                    retries=0,
                )
            except SourceError:
                return posting_id, {}
            return posting_id, detail if isinstance(detail, dict) else {}

        with ThreadPoolExecutor(max_workers=6) as executor:
            for posting_id, detail in executor.map(fetch_detail, detail_candidates):
                if posting_id and detail:
                    details[posting_id] = detail

        roles: list[Role] = []
        for raw in postings:
            posting_id = str(raw.get("id") or raw.get("uuid") or "")
            detail = details.get(posting_id, raw)
            title = str(detail.get("name") or raw.get("name") or "")
            location_info = detail.get("location") or raw.get("location") or {}
            location = ", ".join(
                str(value)
                for value in (
                    location_info.get("city"),
                    location_info.get("region"),
                    location_info.get("country"),
                )
                if value
            )
            job_ad = detail.get("jobAd") or {}
            sections = job_ad.get("sections") or {}
            description = _plain_text(
                " ".join(
                    str(value)
                    for value in (
                        sections.get("jobDescription"),
                        sections.get("qualifications"),
                        sections.get("additionalInformation"),
                    )
                    if value
                )
            )
            apply_url = str(
                detail.get("applyUrl")
                or raw.get("applyUrl")
                or detail.get("ref")
                or raw.get("ref")
                or ""
            )
            roles.append(
                self._role(
                    id=f"smartrecruiters:{company_id}:{posting_id or title}",
                    title=title,
                    location=location,
                    employment_type=_employment_type(title, description),
                    url=apply_url,
                    source="SmartRecruiters official postings API",
                    description=description,
                    requirements=description,
                    posted_date=detail.get("releasedDate") or raw.get("releasedDate"),
                    role_family=_role_family(title, description),
                )
            )
        return roles


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "bytedance": ByteDanceAdapter,
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workday": WorkdayAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "careers_page": CareersPageAdapter,
    "google_careers": GoogleCareersAdapter,
    "apple_careers": AppleCareersAdapter,
}


def _employment_type(title: str, text: str) -> str:
    value = f"{title} {text}".lower()
    if re.search(r"\b(intern|internship)\b", value):
        return "Internship"
    if "co-op" in value or "coop" in value:
        return "Co-op"
    if "contract" in value or "fixed-term" in value:
        return "Fixed-term"
    if "part-time" in value:
        return "Part-time"
    return "Regular"


def _target_match(role: Role, keywords: Iterable[str]) -> bool:
    value = role.text
    return any(keyword.lower() in value for keyword in keywords)


def _china_based(role: Role) -> bool:
    location = role.location.lower()
    return any(
        term in location
        for term in (
            "china",
            "beijing",
            "shanghai",
            "shenzhen",
            "guangzhou",
            "hangzhou",
            "hong kong",
        )
    )


def _normalized_url(url: str) -> str:
    return normalize_application_url(url)


def deduplicate_roles(roles: Iterable[Role]) -> list[Role]:
    unique: list[Role] = []
    source_ids: dict[tuple[str, str, str], Role] = {}
    urls: dict[str, Role] = {}
    exact: dict[tuple[str, str, str], Role] = {}
    for role in roles:
        source_key = (
            normalize_company_name(role.company),
            role.source_type,
            role.source_job_id,
        )
        if role.source_type and role.source_job_id and source_key in source_ids:
            existing = source_ids[source_key]
            if len(role.description) > len(existing.description):
                index = unique.index(existing)
                unique[index] = role
                source_ids[source_key] = role
            continue
        url_key = _normalized_url(role.url)
        if url_key and url_key in urls:
            existing = urls[url_key]
            if len(role.description) > len(existing.description):
                index = unique.index(existing)
                unique[index] = role
                urls[url_key] = role
                if role.source_type and role.source_job_id:
                    source_ids[source_key] = role
            continue
        key = (
            normalize_company_name(role.company),
            normalize_job_title(role.title),
            normalize_location_name(role.location),
        )
        existing = exact.get(key)
        if existing:
            existing_url = _normalized_url(existing.url)
            role_url = _normalized_url(role.url)
            similar_url = (
                not existing_url
                or not role_url
                or SequenceMatcher(None, existing_url, role_url).ratio() >= 0.82
            )
            if similar_url:
                if len(role.description) > len(existing.description):
                    index = unique.index(existing)
                    unique[index] = role
                    exact[key] = role
                continue
        if role.source_type and role.source_job_id:
            source_ids[source_key] = role
        if url_key:
            urls[url_key] = role
        exact[key] = role
        unique.append(role)
    return unique


def _cache_key(source: dict[str, Any]) -> str:
    raw = "|".join(
        str(source.get(key) or "")
        for key in ("company", "adapter", "board_slug", "endpoint", "careers_page")
    )
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("_")[:160] or "unknown"


def _role_from_cache(raw: dict[str, Any], *, stale: bool = False) -> Role:
    data = dict(raw)
    if stale:
        data["source"] = f"{data.get('source', 'Cached source')} (stale cache)"
    allowed = Role.__dataclass_fields__.keys()
    return Role(**{key: data[key] for key in allowed if key in data})


def _load_source_cache(cache_path: Path, source: dict[str, Any]) -> SourceResult | None:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != SOURCE_CACHE_VERSION:
        return None
    entry = data.get("sources", {}).get(_cache_key(source))
    if not isinstance(entry, dict):
        return None
    fetched = entry.get("fetched_at") or entry.get("last_success")
    try:
        fetched_at = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
    except ValueError:
        return None
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched_at > timedelta(days=CACHE_TTL_DAYS):
        return None
    roles = [
        _role_from_cache(item, stale=True)
        for item in entry.get("jobs", [])
        if isinstance(item, dict)
    ]
    if not roles:
        return None
    return SourceResult(
        company=str(source.get("company", "")),
        source_type=str(source.get("ats_type") or source.get("adapter") or ""),
        status="stale_cache",
        jobs=roles,
        fetched_at=datetime.now(timezone.utc),
        endpoint=str(source.get("endpoint") or source.get("careers_page") or ""),
        metadata={
            "last_success": fetched_at.isoformat(),
            "fallback_used": "stale_cache",
            "cache_age_days": (datetime.now(timezone.utc) - fetched_at).days,
        },
    )


def _save_source_cache(
    cache_path: Path,
    source: dict[str, Any],
    result: SourceResult,
) -> None:
    if result.status not in {"success", "success_no_jobs", "partial_success"}:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": SOURCE_CACHE_VERSION, "sources": {}}
    data.setdefault("version", SOURCE_CACHE_VERSION)
    data.setdefault("sources", {})
    now = (result.fetched_at or datetime.now(timezone.utc)).isoformat()
    data["sources"][_cache_key(source)] = {
        "company": result.company,
        "source_type": result.source_type,
        "status": result.status,
        "endpoint": result.endpoint,
        "fetched_at": now,
        "last_success": now,
        "jobs": [asdict(role) for role in result.jobs],
    }
    cache_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class SourceHealth:
    company: str
    status: str
    adapter: str = ""
    endpoint: str = ""
    board_slug: str = ""
    enabled: bool = True
    http_status: int | None = None
    roles_found: int = 0
    internship_roles_found: int = 0
    error_type: str = ""
    error_message: str = ""
    checked_at: str = ""
    recommended_action: str = ""
    message: str = ""
    error_code: str = ""
    suggested_fix: str = ""
    duration_seconds: float = 0.0
    attempt_count: int = 0
    last_success: str = ""
    consecutive_failures: int = 0
    failure_category: str = ""
    fallback_used: str = ""
    detected_source_type: str = ""
    configuration_problems: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        return self.adapter

    @property
    def raw_roles(self) -> int:
        return self.roles_found

    @property
    def internship_roles(self) -> int:
        return self.internship_roles_found

    def as_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "adapter": self.adapter,
            "endpoint": self.endpoint,
            "status": self.status,
            "http_status": self.http_status,
            "roles_found": self.roles_found,
            "internship_roles_found": self.internship_roles_found,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "checked_at": self.checked_at,
            "recommended_action": self.recommended_action,
            "message": self.message,
            "error_code": self.error_code,
            "suggested_fix": self.suggested_fix,
            "duration_seconds": self.duration_seconds,
            "attempt_count": self.attempt_count,
            "last_success": self.last_success,
            "consecutive_failures": self.consecutive_failures,
            "failure_category": self.failure_category,
            "fallback_used": self.fallback_used,
            "detected_source_type": self.detected_source_type,
            "configuration_problems": list(self.configuration_problems),
        }


@dataclass(frozen=True)
class SearchResult:
    roles: list[Role]
    companies_attempted: int
    companies_succeeded: int
    raw_roles_found: int
    failures: tuple[str, ...]
    source_health: tuple[SourceHealth, ...] = ()


class MultiCompanyClient:
    def __init__(self, max_workers: int = 8, cache_path: Path | None = None) -> None:
        self.max_workers = max_workers
        self.cache_path = cache_path or Path("data/source_cache.json")

    def _failure_category(self, exc: Exception) -> str:
        category = str(getattr(exc, "category", "") or "")
        if category == "rate_limited":
            return "rate_limited"
        if category in {"invalid_configuration", "blocked"}:
            return "http_4xx" if str(getattr(exc, "error_code", "")).startswith("HTTP 4") else category
        if category in {"partial_results"}:
            text = str(exc).lower()
            if "timed out" in text or "timeout" in text:
                return "timeout"
            if "nodename" in text or "name or service" in text or "dns" in text:
                return "dns_error"
            return "connection_error"
        if category == "parser_suspected_broken":
            code = str(getattr(exc, "error_code", ""))
            if "JSON" in code or "json" in str(exc).lower():
                return "invalid_json"
            return "schema_changed"
        return "unknown"

    def _result_health(
        self,
        source: dict[str, Any],
        result: SourceResult,
        checked_at: str,
        *,
        configuration_problems: tuple[str, ...] = (),
    ) -> SourceHealth:
        internship_count = sum(
            1
            for role in result.jobs
            if "intern" in f"{role.title} {role.employment_type}".lower()
            or "co-op" in f"{role.title} {role.employment_type}".lower()
            or "coop" in f"{role.title} {role.employment_type}".lower()
        )
        metadata = result.metadata
        if result.status in {"success", "partial_success"}:
            action = "No action needed." if internship_count else "No current internship/co-op postings; keep source enabled."
        elif result.status == "success_no_jobs":
            action = "Zero jobs returned; verify completeness before treating as no openings."
        elif result.status == "stale_cache":
            action = "Using recent cached jobs because today's source fetch failed; repair or revalidate source."
        else:
            action = result.error_message or "Inspect source configuration."
        return SourceHealth(
            company=result.company,
            status=result.status,
            adapter=result.source_type,
            endpoint=result.endpoint or "",
            board_slug=str(source.get("board_slug") or source.get("identifier") or ""),
            enabled=bool(source.get("enabled", True)),
            roles_found=len(result.jobs),
            internship_roles_found=internship_count,
            error_message=result.error_message or "",
            checked_at=checked_at,
            recommended_action=action,
            message=result.error_message or "",
            duration_seconds=float(metadata.get("duration_seconds", 0.0) or 0.0),
            attempt_count=int(metadata.get("attempt_count", 0) or 0),
            last_success=str(metadata.get("last_success", "")),
            consecutive_failures=int(metadata.get("consecutive_failures", 0) or 0),
            failure_category=str(metadata.get("failure_category", "")),
            fallback_used=str(metadata.get("fallback_used", "")),
            detected_source_type=str(source.get("detected_ats_type", "")),
            configuration_problems=configuration_problems,
        )

    def _failure_result(
        self,
        source: dict[str, Any],
        exc: Exception,
        *,
        status: str = "source_failure",
    ) -> SourceResult:
        metadata = {
            "failure_category": self._failure_category(exc),
            "attempt_count": int(source.get("retries", 1)) + 1,
            "fallback_used": "",
        }
        return SourceResult(
            company=str(source.get("company", "Unknown company")),
            source_type=str(source.get("ats_type") or source.get("adapter") or "unknown"),
            status=status,
            jobs=[],
            error_message=f"{type(exc).__name__}: {_sanitize_error_message(str(exc))}",
            fetched_at=datetime.now(timezone.utc),
            endpoint=str(source.get("endpoint") or source.get("careers_page") or ""),
            metadata=metadata,
        )

    def search(self, config: dict[str, Any]) -> SearchResult:
        keywords = [str(value) for value in config.get("keywords", [])]
        sources = [
            _canonical_source_config(source)
            for source in config.get("sources", [])
            if source.get("enabled", True)
        ]
        disabled_sources = [
            _canonical_source_config(source)
            for source in config.get("sources", [])
            if not source.get("enabled", True)
        ]
        roles: list[Role] = []
        failures: list[str] = []
        source_health: list[SourceHealth] = []
        succeeded = 0
        checked_at = datetime.now(timezone.utc).isoformat()

        def fetch(source: dict[str, Any]) -> SourceResult:
            problems = validate_source_config(source)
            if problems:
                raise SourceUnavailable(
                    "; ".join(problems),
                    error_code="CONFIG",
                    category="invalid_configuration",
                    suggested_fix="Fix the company source configuration.",
                )
            adapter_name = str(source["adapter"])
            adapter_type = ADAPTERS.get(adapter_name)
            if adapter_type is None:
                raise SourceUnavailable(
                    f"unknown adapter {adapter_name}",
                    error_code="CONFIG",
                    category="invalid_configuration",
                    suggested_fix="Use one of the supported adapters or implement a new official-source adapter.",
                )
            return adapter_type(source).fetch_result(keywords)

        for source in disabled_sources:
            source_health.append(
                SourceHealth(
                    company=str(source.get("company", "Unknown company")),
                    status="disabled_intentionally",
                    adapter=str(source.get("ats_type") or source.get("adapter", "unknown")),
                    endpoint=str(source.get("endpoint") or source.get("careers_page") or ""),
                    board_slug=str(source.get("board_slug") or source.get("identifier") or ""),
                    enabled=False,
                    checked_at=checked_at,
                    recommended_action=str(source.get("disabled_reason") or "Leave disabled until the official source can be supported."),
                    detected_source_type=str(source.get("detected_ats_type", "")),
                )
            )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(fetch, source): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                company = str(source.get("company", "Unknown company"))
                adapter_name = str(source.get("adapter", "unknown"))
                provider = str(source.get("ats_type") or adapter_name)
                board_slug = str(source.get("board_slug") or source.get("identifier") or "")
                endpoint = str(
                    source.get("endpoint")
                    or source.get("careers_page")
                    or (
                        f"{adapter_name}:{board_slug}" if board_slug else adapter_name
                    )
                )
                try:
                    result = future.result()
                    company_roles = result.jobs
                    roles.extend(company_roles)
                    succeeded += 1
                    _save_source_cache(self.cache_path, source, result)
                    internship_count = sum(
                        1
                        for role in company_roles
                        if "intern" in f"{role.title} {role.employment_type}".lower()
                        or "co-op" in f"{role.title} {role.employment_type}".lower()
                        or "coop" in f"{role.title} {role.employment_type}".lower()
                    )
                    if result.status in {"success", "partial_success"} and internship_count:
                        print(
                            f"[SOURCE] {company}: {len(company_roles)} roles, "
                            f"{internship_count} internship/co-op-like",
                            flush=True,
                        )
                    elif result.status in {"success", "partial_success"} and company_roles:
                        print(
                            f"[SOURCE EMPTY] {company}: {len(company_roles)} roles, "
                            "no internship/co-op-like postings",
                            flush=True,
                        )
                    else:
                        print(
                            f"[SOURCE EMPTY] {company}: no open roles returned",
                            flush=True,
                        )
                    source_health.append(self._result_health(source, result, checked_at))
                except Exception as exc:
                    message = f"{company}: {type(exc).__name__}: {exc}"
                    failures.append(message)
                    status = (
                        "configuration_error"
                        if getattr(exc, "category", "") == "invalid_configuration"
                        else "source_failure"
                    )
                    http_status = None
                    http_match = re.search(r"HTTP\s+(\d+)", str(getattr(exc, "error_code", "")) or str(exc))
                    if http_match:
                        http_status = int(http_match.group(1))
                    fallback = _load_source_cache(self.cache_path, source)
                    if fallback:
                        roles.extend(fallback.jobs)
                        metadata = dict(fallback.metadata)
                        metadata["failure_category"] = self._failure_category(exc)
                        metadata["fallback_used"] = "stale_cache"
                        fallback = SourceResult(
                            company=fallback.company,
                            source_type=fallback.source_type,
                            status="stale_cache",
                            jobs=fallback.jobs,
                            error_message=f"Live fetch failed: {type(exc).__name__}: {_sanitize_error_message(str(exc))}",
                            fetched_at=fallback.fetched_at,
                            endpoint=fallback.endpoint,
                            metadata=metadata,
                        )
                        succeeded += 1
                        source_health.append(self._result_health(source, fallback, checked_at))
                    else:
                        result = self._failure_result(source, exc, status=status)
                        health = self._result_health(
                            source,
                            result,
                            checked_at,
                            configuration_problems=tuple(validate_source_config(source)),
                        )
                        source_health.append(
                            SourceHealth(
                                **{
                                    **health.__dict__,
                                    "http_status": http_status,
                                    "error_type": type(exc).__name__,
                                    "error_code": str(getattr(exc, "error_code", "")),
                                    "suggested_fix": str(
                                        getattr(
                                            exc,
                                            "suggested_fix",
                                            "Inspect the official careers page and update the source configuration.",
                                        )
                                    ),
                                }
                            )
                        )
                    print(f"[SOURCE WARNING] {message}", flush=True)

        raw_count = len(roles)
        roles = [
            role
            for role in roles
            if not _china_based(role)
        ]
        return SearchResult(
            roles=deduplicate_roles(roles),
            companies_attempted=len(sources),
            companies_succeeded=succeeded,
            raw_roles_found=raw_count,
            failures=tuple(failures),
            source_health=tuple(
                sorted(source_health, key=lambda item: (item.status, item.company))
            ),
        )


def load_sources_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "companies" in data and "sources" not in data:
        data["sources"] = data.pop("companies")
    data["sources"] = [
        _canonical_source_config(source)
        for source in data.get("sources", [])
        if isinstance(source, dict)
    ]
    return data


def validate_sources(
    config: dict[str, Any],
    *,
    max_workers: int = 8,
    cache_path: Path | None = None,
) -> tuple[SourceHealth, ...]:
    client = MultiCompanyClient(max_workers=max_workers, cache_path=cache_path)
    result = client.search(config)
    return result.source_health
