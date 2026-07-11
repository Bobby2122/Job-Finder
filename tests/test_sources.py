from __future__ import annotations

import json
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
    WorkdayAdapter,
    load_sources_config,
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
        self.assertEqual(statuses["Fixture Empty"], "partial_results")
        self.assertEqual(statuses["Fixture Broken"], "invalid_configuration")
        broken = next(
            item for item in result.source_health if item.company == "Fixture Broken"
        )
        self.assertEqual(broken.error_type, "SourceUnavailable")
        self.assertTrue(broken.recommended_action)


if __name__ == "__main__":
    unittest.main()
