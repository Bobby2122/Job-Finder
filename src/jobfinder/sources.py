from __future__ import annotations

import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .models import Role


class SourceError(RuntimeError):
    pass


def _plain_text(value: str | None) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<(?:br|/p|/li|/div|/h\d)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _role_family(title: str, text: str = "") -> str:
    value = f"{title} {text}".lower()
    families = (
        ("Machine Learning / AI", ("machine learning", "artificial intelligence", " ai ", "ml engineer")),
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
    timeout: float = 20.0,
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
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.4 * (2**attempt))
    raise SourceError(f"{url}: {type(last_error).__name__}")


class SourceAdapter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def company(self) -> str:
        return str(self.config["company"])

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
        roles = client.search(
            keywords,
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
        token = self.config["identifier"]
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        data = _request_json(url)
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
        site = self.config["identifier"]
        region = "api.eu.lever.co" if self.config.get("region") == "eu" else "api.lever.co"
        url = f"https://{region}/v0/postings/{site}?mode=json"
        data = _request_json(url)
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
        board = self.config["identifier"]
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        data = _request_json(url)
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
        for keyword in source_keywords[: int(self.config.get("keyword_limit", 8))]:
            data = _request_json(
                endpoint,
                method="POST",
                payload={
                    "appliedFacets": {},
                    "limit": 20,
                    "offset": 0,
                    "searchText": keyword,
                },
            )
            postings = data.get("jobPostings", []) if isinstance(data, dict) else []
            for raw in postings:
                external_path = str(raw.get("externalPath") or "")
                raw_found[external_path or str(raw.get("title") or "")] = raw

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


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "bytedance": ByteDanceAdapter,
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "workday": WorkdayAdapter,
}


def _employment_type(title: str, text: str) -> str:
    value = f"{title} {text}".lower()
    if "intern" in value:
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
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def deduplicate_roles(roles: Iterable[Role]) -> list[Role]:
    unique: list[Role] = []
    exact: dict[tuple[str, str, str], Role] = {}
    for role in roles:
        key = (
            _normalized_text(role.company),
            _normalized_text(role.title),
            _normalized_text(role.location),
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
class SearchResult:
    roles: list[Role]
    companies_attempted: int
    companies_succeeded: int
    raw_roles_found: int
    failures: tuple[str, ...]


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
        succeeded = 0

        def fetch(source: dict[str, Any]) -> list[Role]:
            adapter_name = str(source["adapter"])
            adapter_type = ADAPTERS.get(adapter_name)
            if adapter_type is None:
                raise SourceError(f"unknown adapter {adapter_name}")
            return adapter_type(source).fetch(keywords)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(fetch, source): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                company = str(source.get("company", "Unknown company"))
                try:
                    company_roles = future.result()
                    roles.extend(company_roles)
                    succeeded += 1
                    print(f"[SOURCE] {company}: {len(company_roles)} roles")
                except Exception as exc:
                    message = f"{company}: {type(exc).__name__}: {exc}"
                    failures.append(message)
                    print(f"[SOURCE WARNING] {message}")

        raw_count = len(roles)
        roles = [
            role
            for role in roles
            if not _china_based(role) and _target_match(role, keywords)
        ]
        return SearchResult(
            roles=deduplicate_roles(roles),
            companies_attempted=len(sources),
            companies_succeeded=succeeded,
            raw_roles_found=raw_count,
            failures=tuple(failures),
        )


def load_sources_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
