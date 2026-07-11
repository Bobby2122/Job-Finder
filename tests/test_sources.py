from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobfinder.sources import (
    AppleCareersAdapter,
    AshbyAdapter,
    GoogleCareersAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    MultiCompanyClient,
    SourceError,
    WorkdayAdapter,
    detect_ats_from_url,
    load_sources_config,
    validate_source_config,
)


ROOT = Path(__file__).resolve().parents[1]


class SourceAdapterTests(unittest.TestCase):
    def test_sources_config_records_health_metadata_fields(self):
        config = load_sources_config(ROOT / "config/sources.json")
        for source in config["sources"]:
            self.assertIn("ats_type", source)
            self.assertIn("board_slug", source)
            self.assertIn("enabled", source)
            self.assertIn("latest_status", source)
            self.assertTrue(source.get("endpoint") or source.get("careers_page") or source["adapter"] == "bytedance")

    def test_greenhouse_adapter_parses_fixture(self):
        payload = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Machine Learning Intern - Spring 2027",
                    "absolute_url": "https://job.example/123",
                    "content": "<p>Build machine learning models.</p>",
                    "location": {"name": "San Francisco, CA"},
                    "departments": [{"name": "AI"}],
                    "updated_at": "2026-07-01",
                }
            ]
        }
        with patch("jobfinder.sources._request_json", return_value=payload):
            roles = GreenhouseAdapter(
                {
                    "company": "Fixture Co",
                    "adapter": "greenhouse",
                    "ats_type": "greenhouse",
                    "identifier": "fixture",
                    "board_slug": "fixture",
                }
            ).fetch([])
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].employment_type, "Internship")
        self.assertEqual(roles[0].country, "United States")

    def test_lever_adapter_parses_fixture(self):
        payload = [
            {
                "id": "abc",
                "text": "Data Science Intern",
                "hostedUrl": "https://jobs.example/abc",
                "descriptionPlain": "Use statistics and machine learning.",
                "additionalPlain": "",
                "lists": [],
                "categories": {
                    "location": "New York, NY",
                    "commitment": "Internship",
                    "team": "Data",
                    "department": "AI",
                },
                "createdAt": 1,
            }
        ]
        with patch("jobfinder.sources._request_json", return_value=payload):
            roles = LeverAdapter(
                {
                    "company": "Fixture Lever",
                    "adapter": "lever",
                    "ats_type": "lever",
                    "identifier": "fixture",
                    "board_slug": "fixture",
                }
            ).fetch([])
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].title, "Data Science Intern")

    def test_ashby_adapter_parses_fixture(self):
        payload = {
            "jobs": [
                {
                    "title": "AI Platform Intern",
                    "jobUrl": "https://jobs.example/ai",
                    "descriptionPlain": "Build LLM evaluation and RAG systems.",
                    "location": "Remote, United States",
                    "employmentType": "Internship",
                    "department": "Engineering",
                    "publishedAt": "2026-07-01",
                    "isListed": True,
                }
            ]
        }
        with patch("jobfinder.sources._request_json", return_value=payload):
            roles = AshbyAdapter(
                {
                    "company": "Fixture Ashby",
                    "adapter": "ashby",
                    "ats_type": "ashby",
                    "identifier": "fixture",
                    "board_slug": "fixture",
                }
            ).fetch([])
        self.assertEqual(len(roles), 1)
        self.assertIn("AI", roles[0].role_family)

    def test_workday_adapter_posts_body_and_paginates_fixture(self):
        calls: list[dict] = []

        def fake_request(url, **kwargs):
            calls.append({"url": url, **kwargs})
            payload = kwargs.get("payload", {})
            if url.endswith("/jobs"):
                if payload["offset"] == 0:
                    return {
                        "total": 2,
                        "jobPostings": [
                            {
                                "title": "Risk Modeling Intern",
                                "externalPath": "/job/one",
                                "locationsText": "Boston, MA",
                                "postedOn": "2026-07-01",
                            }
                        ],
                    }
                return {
                    "total": 2,
                    "jobPostings": [
                        {
                            "title": "Data Science Intern",
                            "externalPath": "/job/two",
                            "locationsText": "Seattle, WA",
                            "postedOn": "2026-07-01",
                        }
                    ],
                }
            return {
                "jobPostingInfo": {
                    "title": "Risk Modeling Intern",
                    "location": "Boston, MA",
                    "timeType": "Internship",
                    "externalUrl": "https://workday.example/job",
                    "jobDescription": "Use forecasting, risk, statistics, and optimization.",
                    "postedOn": "2026-07-01",
                }
            }

        with patch("jobfinder.sources._request_json", side_effect=fake_request):
            roles = WorkdayAdapter(
                {
                    "company": "Fixture Workday",
                    "adapter": "workday",
                    "ats_type": "workday",
                    "endpoint": "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/Site/jobs",
                    "keywords": ["intern"],
                    "keyword_limit": 1,
                    "page_size": 1,
                    "max_pages_per_keyword": 2,
                    "detail_limit": 2,
                }
            ).fetch([])

        search_calls = [call for call in calls if call["url"].endswith("/jobs")]
        self.assertEqual([call["payload"]["offset"] for call in search_calls], [0, 1])
        self.assertEqual(search_calls[0]["method"], "POST")
        self.assertIn("appliedFacets", search_calls[0]["payload"])
        self.assertEqual(len(roles), 2)

    def test_google_careers_adapter_parses_official_structured_fixture(self):
        html = (ROOT / "tests/fixtures/google_careers.html").read_text(
            encoding="utf-8"
        )
        roles = GoogleCareersAdapter(
            {
                "company": "Google",
                "adapter": "google_careers",
                "ats_type": "official_google_careers",
                "endpoint": "https://www.google.com/about/careers/applications/jobs/results/?q=intern&location=United%20States",
                "company_size_category": "Big tech / famous lab",
            }
        )._parse_html(html, "fixture")
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "Google")
        self.assertIn("Student Researcher", roles[0].title)
        self.assertEqual(roles[0].country, "United States")
        self.assertIn("Mountain View", roles[0].location)
        self.assertIn("google.com/about/careers", roles[0].url)

    def test_apple_careers_adapter_parses_official_html_fixture(self):
        html = (ROOT / "tests/fixtures/apple_careers.html").read_text(
            encoding="utf-8"
        )
        roles = AppleCareersAdapter(
            {
                "company": "Apple",
                "adapter": "apple_careers",
                "ats_type": "official_apple_careers",
                "endpoint": "https://jobs.apple.com/en-us/search?search=intern&location=united-states-USA",
                "company_size_category": "Big tech / famous lab",
            }
        )._parse_html(html, "fixture")
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0].company, "Apple")
        self.assertEqual(roles[0].employment_type, "Internship")
        self.assertIn("jobs.apple.com/en-us/details/200000001", roles[0].url)

    def test_multicompany_health_distinguishes_empty_success_from_failure(self):
        config = {
            "keywords": ["intern"],
            "sources": [
                {
                    "company": "Fixture Empty",
                    "adapter": "greenhouse",
                    "ats_type": "greenhouse",
                    "board_slug": "empty",
                    "endpoint": "https://example.test/empty",
                },
                {
                    "company": "Fixture Broken",
                    "adapter": "unknown_adapter",
                    "ats_type": "unknown_adapter",
                    "board_slug": "",
                },
            ],
        }

        with patch("jobfinder.sources._request_json", return_value={"jobs": []}):
            result = MultiCompanyClient(max_workers=1).search(config)

        self.assertEqual(result.companies_succeeded, 1)
        statuses = {item.company: item.status for item in result.source_health}
        self.assertEqual(statuses["Fixture Empty"], "success_no_jobs")
        self.assertEqual(statuses["Fixture Broken"], "configuration_error")
        broken = next(
            item for item in result.source_health if item.company == "Fixture Broken"
        )
        self.assertEqual(broken.error_type, "SourceUnavailable")
        self.assertTrue(broken.recommended_action)

    def test_ats_auto_detection_extracts_tokens(self):
        self.assertEqual(
            detect_ats_from_url("https://boards.greenhouse.io/example")["board_token"],
            "example",
        )
        self.assertEqual(
            detect_ats_from_url("https://jobs.lever.co/acme")["site_token"],
            "acme",
        )
        self.assertEqual(
            detect_ats_from_url("https://jobs.ashbyhq.com/acme")["board_name"],
            "acme",
        )
        workday = detect_ats_from_url(
            "https://acme.wd5.myworkdayjobs.com/External"
        )
        self.assertEqual(workday["type"], "workday")
        self.assertEqual(workday["tenant"], "acme")
        self.assertEqual(workday["site"], "External")

    def test_nested_source_config_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(
                json.dumps(
                    {
                        "keywords": ["intern"],
                        "companies": [
                            {
                                "company": "Nested Co",
                                "careers_url": "https://boards.greenhouse.io/nested",
                                "source": {
                                    "type": "greenhouse",
                                    "board_token": "nested",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = load_sources_config(path)

        source = config["sources"][0]
        self.assertEqual(source["adapter"], "greenhouse")
        self.assertEqual(source["board_slug"], "nested")
        self.assertEqual(validate_source_config(source), [])

    def test_invalid_company_configuration_does_not_crash_run(self):
        config = {
            "keywords": ["intern"],
            "sources": [
                {
                    "company": "Bad Workday",
                    "adapter": "workday",
                    "ats_type": "workday",
                }
            ],
        }
        result = MultiCompanyClient(max_workers=1).search(config)
        health = result.source_health[0]
        self.assertEqual(health.status, "configuration_error")
        self.assertIn("workday", health.recommended_action.lower())

    def test_stale_cache_fallback_is_used_after_failure(self):
        payload = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Data Science Intern",
                    "absolute_url": "https://job.example/123",
                    "content": "Use statistics.",
                    "location": {"name": "Atlanta, GA"},
                }
            ]
        }
        config = {
            "keywords": ["intern"],
            "sources": [
                {
                    "company": "Cache Co",
                    "adapter": "greenhouse",
                    "ats_type": "greenhouse",
                    "board_slug": "cacheco",
                    "endpoint": "https://example.test/cacheco",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "source_cache.json"
            with patch("jobfinder.sources._request_json", return_value=payload):
                first = MultiCompanyClient(max_workers=1, cache_path=cache).search(config)
            with patch(
                "jobfinder.sources._request_json",
                side_effect=SourceError("network down", category="partial_results"),
            ):
                second = MultiCompanyClient(max_workers=1, cache_path=cache).search(config)

        self.assertEqual(first.source_health[0].status, "success")
        self.assertEqual(second.source_health[0].status, "stale_cache")
        self.assertEqual(len(second.roles), 1)
        self.assertIn("stale cache", second.roles[0].source)

    def test_workday_partial_detail_failure_is_reported(self):
        calls: list[str] = []

        def fake_request(url, **kwargs):
            calls.append(url)
            if url.endswith("/jobs"):
                return {
                    "total": 1,
                    "jobPostings": [
                        {
                            "title": "Risk Modeling Intern",
                            "externalPath": "/job/one",
                            "locationsText": "Boston, MA",
                            "postedOn": "2026-07-01",
                        }
                    ],
                }
            raise SourceError("detail failed", category="partial_results")

        with patch("jobfinder.sources._request_json", side_effect=fake_request):
            result = WorkdayAdapter(
                {
                    "company": "Fixture Workday",
                    "adapter": "workday",
                    "ats_type": "workday",
                    "endpoint": "https://tenant.wd1.myworkdayjobs.com/wday/cxs/tenant/Site/jobs",
                    "keywords": ["intern"],
                    "page_size": 1,
                    "max_pages_per_keyword": 1,
                    "detail_limit": 1,
                }
            ).fetch_result([])

        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.metadata["detail_failures"], 1)
        self.assertEqual(len(result.jobs), 1)

    def test_stable_source_ids_are_preserved(self):
        payload = {
            "jobs": [
                {
                    "id": 456,
                    "title": "Machine Learning Intern",
                    "absolute_url": "https://job.example/456",
                    "content": "Build models.",
                    "location": {"name": "Seattle, WA"},
                }
            ]
        }
        with patch("jobfinder.sources._request_json", return_value=payload):
            role = GreenhouseAdapter(
                {
                    "company": "Fixture Co",
                    "adapter": "greenhouse",
                    "ats_type": "greenhouse",
                    "board_slug": "fixture",
                }
            ).fetch([])[0]
        self.assertEqual(role.id, "greenhouse:fixture:456")
        self.assertEqual(role.source_type, "greenhouse")
        self.assertEqual(role.source_company_key, "fixture")
        self.assertEqual(role.source_job_id, "456")


if __name__ == "__main__":
    unittest.main()
