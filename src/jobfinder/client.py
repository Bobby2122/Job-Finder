from __future__ import annotations

import json
import time
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import Job


class ByteDanceClientError(RuntimeError):
    pass


class ByteDanceClient:
    BASE_URL = (
        "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts"
    )

    def __init__(self, timeout: float = 20.0, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries

    @staticmethod
    def _payload(keyword: str, limit: int, offset: int) -> dict[str, Any]:
        return {
            "keyword": keyword,
            "limit": limit,
            "offset": offset,
            "job_category_id_list": [],
            "tag_id_list": [],
            "location_code_list": [],
            "subject_id_list": [],
            "recruitment_id_list": [],
            "portal_type": 2,
            "job_function_id_list": [],
            "storefront_id_list": [],
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.BASE_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Language": "en-US",
                "website-path": "en",
                "x-tt-env": "boe_epam_api",
                "Origin": "https://joinbytedance.com",
                "User-Agent": "BobbyOpportunityTracker/0.1",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    result = json.load(response)
                if result.get("code") not in (None, 0):
                    raise ByteDanceClientError(
                        f"ByteDance API returned code {result.get('code')}"
                    )
                if not isinstance(result.get("data"), dict):
                    raise ByteDanceClientError(
                        "ByteDance API response did not contain a data object"
                    )
                return result
            except (
                HTTPError,
                URLError,
                TimeoutError,
                IncompleteRead,
                RemoteDisconnected,
                json.JSONDecodeError,
            ) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(0.5 * (2**attempt))
        raise ByteDanceClientError(
            f"ByteDance careers request failed after {self.retries + 1} attempts: "
            f"{last_error}"
        )

    def search(
        self,
        keywords: Iterable[str],
        page_size: int = 20,
        max_pages_per_keyword: int = 4,
    ) -> list[Job]:
        jobs: dict[str, Job] = {}
        successful_queries = 0
        query_errors: list[ByteDanceClientError] = []
        for keyword in keywords:
            for page in range(max_pages_per_keyword):
                try:
                    result = self._post(
                        self._payload(keyword, page_size, page * page_size)
                    )
                except ByteDanceClientError as exc:
                    query_errors.append(exc)
                    break
                data = result["data"]
                raw_jobs = data.get("job_post_list")
                if not isinstance(raw_jobs, list):
                    raise ByteDanceClientError(
                        "ByteDance API response omitted job_post_list"
                    )
                successful_queries += 1
                for raw in raw_jobs:
                    try:
                        job = Job.from_api(raw)
                    except (KeyError, TypeError, ValueError):
                        continue
                    jobs[job.id] = job
                total = int(data.get("count") or len(raw_jobs))
                if not raw_jobs or (page + 1) * page_size >= total:
                    break
        if not successful_queries and query_errors:
            raise query_errors[-1]
        if successful_queries and not jobs:
            raise ByteDanceClientError(
                "All ByteDance searches returned zero jobs; refusing to produce "
                "a potentially misleading empty report"
            )
        return list(jobs.values())


def load_fixture(path: Path) -> list[Job]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("data", {}).get("job_post_list", [])
    if not isinstance(raw, list):
        raise ValueError("Fixture must be a job list or an API response object")
    return [Job.from_api(item) for item in raw]
