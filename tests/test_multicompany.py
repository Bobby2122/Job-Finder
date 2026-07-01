from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from jobfinder.models import Role, Score, ScoredJob
from jobfinder.reporting import build_report, select_buckets
from jobfinder.sources import deduplicate_roles


ROOT = Path(__file__).resolve().parents[1]


def make_role(index: int, bucket: str, title: str | None = None) -> ScoredJob:
    role = Role.normalized(
        id=f"{bucket.lower()}-{index}",
        company=f"{bucket} Company {index}",
        title=title or f"{bucket} Data Science Intern {index}",
        location="Atlanta, GA",
        employment_type="Internship",
        url=f"https://example.com/{bucket.lower()}/{index}",
        source="Official careers API",
        description="Python SQL statistics machine learning experimentation",
        requirements="Undergraduate students preferred",
        posted_date="2026-07-01",
        role_family=(
            "Machine Learning / AI"
            if bucket == "Reach"
            else "Data Science"
            if bucket == "Target"
            else "Analytics"
        ),
        company_size_category=(
            "Big tech / famous lab"
            if bucket == "Reach"
            else "Mid-size tech"
            if bucket == "Target"
            else "Insurance/risk"
        ),
        source_category="Test source category",
    )
    score = Score(
        skill_fit=8.0,
        learning_value=8.0,
        accessibility=8.0,
        timing_fit=9.0,
        location_fit=9.0,
        career_value=7.5,
        overall=8.1 - index / 100,
        relevant=True,
        geography_ok=True,
        why_match=("Uses Python, statistics, and modeling",),
        concerns=("Prepare one stronger applied project",),
        competitiveness="High" if bucket == "Reach" else "Medium",
        bucket=bucket,
    )
    return ScoredJob(role, score, is_new=True)


class MultiCompanyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "config/profile.json").read_text(encoding="utf-8")
        )

    def test_role_normalization(self):
        item = make_role(1, "Target")
        role = item.job
        self.assertEqual(role.company, "Target Company 1")
        self.assertEqual(role.employment_type, "Internship")
        self.assertEqual(role.requirements, "Undergraduate students preferred")
        self.assertEqual(role.source, "Official careers API")
        self.assertEqual(role.role_family, "Data Science")
        self.assertEqual(role.country, "United States")
        self.assertEqual(role.start_year_or_season, "Flexible/unspecified")

    def test_deduplication_uses_company_title_location_and_url(self):
        original = make_role(1, "Target").job
        duplicate = Role.normalized(
            id="duplicate",
            company=original.company,
            title=original.title,
            location="Atlanta, GA",
            employment_type="Internship",
            url=f"{original.url}?ref=campus",
            source="Official careers API",
            description=(
                "A substantially longer description with Python, SQL, statistics, "
                "machine learning, experimentation, mentorship, and project ownership."
            ),
            role_family="Data Science",
        )
        unique = deduplicate_roles([original, duplicate])
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].id, "duplicate")

    def test_reach_target_safe_classification_selection(self):
        scored = [
            make_role(index, bucket)
            for bucket in ("Reach", "Target", "Safe")
            for index in range(5)
        ]
        buckets = select_buckets(scored, floor=5.8, per_bucket=5)
        for bucket in ("Reach", "Target", "Safe"):
            self.assertEqual(len(buckets[bucket].roles), 5)
            self.assertTrue(
                all(item.score.bucket == bucket for item in buckets[bucket].roles)
            )

    def test_report_contains_exactly_five_roles_per_bucket(self):
        scored = [
            make_role(index, bucket)
            for bucket in ("Reach", "Target", "Safe")
            for index in range(5)
        ]
        report = build_report(
            scored,
            self.profile,
            datetime.now(timezone.utc),
        )
        self.assertIn("## A. Reach Roles (5)", report)
        self.assertIn("## B. Target Roles (5)", report)
        self.assertIn("## C. Safe Roles (5)", report)
        self.assertIn("- **Roles selected:** 15", report)
        self.assertEqual(report.count("### ["), 15)

    def test_senior_role_is_never_selected(self):
        scored = [
            make_role(index, bucket)
            for bucket in ("Reach", "Target", "Safe")
            for index in range(5)
        ]
        senior = make_role(99, "Target", title="Sr. Data Scientist")
        buckets = select_buckets([senior, *scored], floor=5.8, per_bucket=5)
        selected_titles = {
            item.job.title
            for selection in buckets.values()
            for item in selection.roles
        }
        self.assertNotIn("Sr. Data Scientist", selected_titles)

    def test_company_diversity_caps_dominant_company_when_alternatives_exist(self):
        dominant = [
            replace(
                make_role(index, "Target"),
                job=replace(make_role(index, "Target").job, company="Famous Co"),
            )
            for index in range(5)
        ]
        alternatives = [
            make_role(index + 10, "Target") for index in range(5)
        ]
        other_buckets = [
            make_role(index, bucket)
            for bucket in ("Reach", "Safe")
            for index in range(5)
        ]
        buckets = select_buckets(
            [*dominant, *alternatives, *other_buckets],
            floor=5.8,
            per_bucket=5,
        )
        selected = [
            item for selection in buckets.values() for item in selection.roles
        ]
        self.assertLessEqual(
            sum(item.job.company == "Famous Co" for item in selected),
            2,
        )


if __name__ == "__main__":
    unittest.main()
