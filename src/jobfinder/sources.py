from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from typing import Any, Iterable
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
        category: str = "parser_failure",
        suggested_fix: str = "Inspect the official careers page and update the adapter or parser.",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.category = category
        self.suggested_fix = suggested_fix


class SourceUnavailable(SourceError):
    pass


def _source_category_for_http(code: int) -> tuple[str, str]:
    if code == 404:
        return "invalid_endpoint", "Verify the ATS provider and board slug against the official careers page."
    if code == 403:
        return "blocked", "Use an official API endpoint where available or add approved headers for the provider."
    if code == 422:
        return "invalid_endpoint", "Verify request body, tenant, site, and pagination parameters."
    if 500 <= code < 600:
        return "temporary_network_failure", "Retry later; provider returned a server-side error."
    return "parser_failure", "Inspect provider response and update the adapter."


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
            if attempt < retries:
                time.sleep(0.4 * (2**attempt))
        except (
            URLError,
            TimeoutError,
            IncompleteRead,
            RemoteDisconnected,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * (2**attempt))
    if isinstance(last_error, HTTPError):
        category, suggested_fix = _source_category_for_http(last_error.code)
        raise SourceUnavailable(
            f"{url}: HTTP {last_error.code}",
            error_code=f"HTTP {last_error.code}",
            category=category,
            suggested_fix=suggested_fix,
        )
    category = "temporary_network_failure" if isinstance(
        last_error,
        (URLError, TimeoutError, IncompleteRead, RemoteDisconnected),
    ) else "parser_failure"
    raise SourceError(
        f"{url}: {type(last_error).__name__}",
        error_code=type(last_error).__name__ if last_error else "Unknown",
        category=category,
        suggested_fix="Retry later if this is transient; otherwise inspect the official careers page.",
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

    def _role(self, **values: Any) -> Role:
        return Role.normalized(
            company=self.company,
            company_size_category=str(
                self.config.get("company_size_category", "Mid-size tech")
            ),
            source_category=str(
                self.config.get("source_category", "Mid-size tech")
            ),
            **values,
        )

    def fetch(self, keywords: list[str]) -> list[Role]:
        raise NotImplementedError


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
            roles.append(
                self._role(
                    id=f"greenhouse:{token}:{raw.get('id')}",
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
            roles.append(
                self._role(
                    id=f"lever:{site}:{raw.get('id')}",
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
            description = _plain_text(
                raw.get("descriptionPlain") or raw.get("descriptionHtml")
            )
            roles.append(
                self._role(
                    id=f"ashby:{board}:{raw.get('jobUrl') or raw.get('applyUrl')}",
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


class CareersPageAdapter(SourceAdapter):
    def fetch(self, keywords: list[str]) -> list[Role]:
        url = str(self.config.get("careers_page") or self.config.get("endpoint") or "")
        if not url:
            raise SourceUnavailable(
                "careers_page adapter requires careers_page or endpoint",
                error_code="CONFIG",
                category="invalid_endpoint",
                suggested_fix="Add the company's official careers page URL.",
            )
        try:
            request = Request(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "BobbyOpportunityTracker/0.2",
                },
            )
            with urlopen(request, timeout=float(self.config.get("timeout", 15))) as response:
                html_text = response.read(1_500_000).decode("utf-8", errors="replace")
        except HTTPError as exc:
            category, suggested_fix = _source_category_for_http(exc.code)
            raise SourceUnavailable(
                f"{url}: HTTP {exc.code}",
                error_code=f"HTTP {exc.code}",
                category=category,
                suggested_fix=suggested_fix,
            )
        except (URLError, TimeoutError) as exc:
            raise SourceError(
                f"{url}: {type(exc).__name__}",
                error_code=type(exc).__name__,
                category="temporary_network_failure",
                suggested_fix="Retry later or replace with a stable official ATS API if available.",
            )

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
            category="parser_failure",
            suggested_fix="Add a dedicated adapter for this official careers page or replace it with a stable ATS API.",
        )


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
    exact: dict[tuple[str, str, str], Role] = {}
    for role in roles:
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
        exact[key] = role
        unique.append(role)
    return unique


@dataclass(frozen=True)
class SourceHealth:
    company: str
    status: str
    provider: str = ""
    endpoint: str = ""
    board_slug: str = ""
    enabled: bool = True
    raw_roles: int = 0
    internship_roles: int = 0
    message: str = ""
    error_code: str = ""
    suggested_fix: str = ""


@dataclass(frozen=True)
class SearchResult:
    roles: list[Role]
    companies_attempted: int
    companies_succeeded: int
    raw_roles_found: int
    failures: tuple[str, ...]
    source_health: tuple[SourceHealth, ...] = ()


class MultiCompanyClient:
    def __init__(self, max_workers: int = 8) -> None:
        self.max_workers = max_workers

    def search(self, config: dict[str, Any]) -> SearchResult:
        keywords = [str(value) for value in config.get("keywords", [])]
        sources = [
            source
            for source in config.get("sources", [])
            if source.get("enabled", True)
        ]
        roles: list[Role] = []
        failures: list[str] = []
        source_health: list[SourceHealth] = []
        succeeded = 0

        def fetch(source: dict[str, Any]) -> list[Role]:
            adapter_name = str(source["adapter"])
            adapter_type = ADAPTERS.get(adapter_name)
            if adapter_type is None:
                raise SourceUnavailable(
                    f"unknown adapter {adapter_name}",
                    error_code="CONFIG",
                    category="invalid_endpoint",
                    suggested_fix="Use one of the supported adapters or implement a new official-source adapter.",
                )
            return adapter_type(source).fetch(keywords)

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
                    company_roles = future.result()
                    roles.extend(company_roles)
                    succeeded += 1
                    internship_count = sum(
                        1
                        for role in company_roles
                        if "intern" in f"{role.title} {role.employment_type}".lower()
                        or "co-op" in f"{role.title} {role.employment_type}".lower()
                        or "coop" in f"{role.title} {role.employment_type}".lower()
                    )
                    if company_roles and internship_count:
                        status = "working"
                        print(
                            f"[SOURCE] {company}: {len(company_roles)} roles, "
                            f"{internship_count} internship/co-op-like",
                            flush=True,
                        )
                    elif company_roles:
                        status = "empty_but_healthy"
                        print(
                            f"[SOURCE EMPTY] {company}: {len(company_roles)} roles, "
                            "no internship/co-op-like postings",
                            flush=True,
                        )
                    else:
                        status = "empty_but_healthy"
                        print(
                            f"[SOURCE EMPTY] {company}: no open roles returned",
                            flush=True,
                        )
                    source_health.append(
                        SourceHealth(
                            company=company,
                            status=status,
                            provider=provider,
                            endpoint=endpoint,
                            board_slug=board_slug,
                            enabled=bool(source.get("enabled", True)),
                            raw_roles=len(company_roles),
                            internship_roles=internship_count,
                        )
                    )
                except Exception as exc:
                    message = f"{company}: {type(exc).__name__}: {exc}"
                    failures.append(message)
                    status = getattr(exc, "category", "")
                    if not status:
                        status = (
                            "invalid_endpoint"
                            if isinstance(exc, SourceUnavailable)
                            else "parser_failure"
                        )
                    source_health.append(
                        SourceHealth(
                            company=company,
                            status=status,
                            provider=provider,
                            endpoint=endpoint,
                            board_slug=board_slug,
                            enabled=bool(source.get("enabled", True)),
                            message=f"{type(exc).__name__}: {exc}",
                            error_code=str(getattr(exc, "error_code", "")),
                            suggested_fix=str(
                                getattr(
                                    exc,
                                    "suggested_fix",
                                    "Inspect the official careers page and update the source configuration.",
                                )
                            ),
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
    return json.loads(path.read_text(encoding="utf-8"))
