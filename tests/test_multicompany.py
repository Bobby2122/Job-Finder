from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from jobfinder.models import Role, Score, ScoredJob
from jobfinder.reporting import build_report, select_buckets
from jobfinder.scoring import company_size_group
from jobfinder.sources import deduplicate_roles


ROOT = Path(__file__).resolve().parents[1]


def make_role(
    index: int,
    bucket: str,
    title: str | None = None,
    size_group: str | None = None,
) -> ScoredJob:
    size_group = size_group or (
        "Large" if bucket == "Reach" else "Mid"
    )
    category = {
        "Large": "Big tech / famous lab",
        "Mid": "Mid-size tech",
        "Small": "Startup",
    }[size_group]
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
        company_size_category=category,
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
        internship_clarity=10.0,
        competition_ease={"Large": 3.5, "Mid": 7.0, "Small": 9.0}[size_group],
        requirement_ease=9.0,
        us_stability=10.0,
    )
    return ScoredJob(role, score, is_new=True)


def balanced_roles() -> list[ScoredJob]:
    plan = {
        "Reach": {"Large": 2, "Mid": 2, "Small": 1},
        "Target": {"Large": 2, "Mid": 2, "Small": 1},
        "Safe": {"Large": 1, "Mid": 1, "Small": 3},
    }
    roles: list[ScoredJob] = []
    index = 0
    for bucket, sizes in plan.items():
        for size, count in sizes.items():
            for _ in range(count):
                roles.append(make_role(index, bucket, size_group=size))
                index += 1
    return roles


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
        scored = balanced_roles()
        buckets = select_buckets(scored, floor=5.8, per_bucket=5)
        for bucket in ("Reach", "Target", "Safe"):
            self.assertEqual(len(buckets[bucket].roles), 5)
            self.assertTrue(
                all(item.score.bucket == bucket for item in buckets[bucket].roles)
            )
            self.assertEqual(
                {company_size_group(item.job) for item in buckets[bucket].roles},
                {"Large", "Mid", "Small"},
            )
        selected = [
            item for selection in buckets.values() for item in selection.roles
        ]
        size_counts = {
            size: sum(company_size_group(item.job) == size for item in selected)
            for size in ("Large", "Mid", "Small")
        }
        self.assertEqual(size_counts, {"Large": 5, "Mid": 5, "Small": 5})

    def test_report_contains_exactly_five_roles_per_bucket(self):
        scored = balanced_roles()
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
        scored = balanced_roles()
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
                make_role(index + 100, "Target", size_group="Mid"),
                job=replace(
                    make_role(index + 100, "Target", size_group="Mid").job,
                    company="Famous Co",
                ),
            )
            for index in range(5)
        ]
        buckets = select_buckets(
            [*dominant, *balanced_roles()],
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

    def test_viewed_role_is_downranked_below_fresh_alternative(self):
        scored = balanced_roles()
        viewed = replace(
            scored[0],
            score=replace(scored[0].score, overall=8.5),
            tracking_status="Viewed",
        )
        fresh = make_role(200, "Reach", size_group="Large")
        fresh = replace(
            fresh,
            score=replace(fresh.score, overall=8.0),
            tracking_status="New",
        )
        buckets = select_buckets(
            [viewed, fresh, *scored[1:]],
            floor=5.8,
            per_bucket=5,
        )
        reach_ids = {item.job.id for item in buckets["Reach"].roles}

        self.assertIn(fresh.job.id, reach_ids)
        self.assertNotIn(viewed.job.id, reach_ids)

    def test_adjacent_bucket_fallback_fills_empty_buckets(self):
        scored = [
            replace(make_role(index, "Target", size_group="Mid"), score=replace(
                make_role(index, "Target", size_group="Mid").score,
                bucket="Target",
                overall=7.0 - index / 100,
            ))
            for index in range(15)
        ]
        buckets = select_buckets(scored, floor=5.8, per_bucket=5)

        self.assertEqual(len(buckets["Reach"].roles), 5)
        self.assertEqual(len(buckets["Safe"].roles), 5)
        self.assertGreater(
            buckets["Reach"].diagnostics.adjacent_fallback_selected,
            0,
        )
        self.assertTrue(
            all(item.score.bucket == "Target" for item in buckets["Reach"].roles)
        )

    def test_adaptive_floor_recovers_relevant_roles_below_preferred_floor(self):
        low_scored = [
            replace(
                item,
                score=replace(item.score, overall=3.8),
            )
            for item in balanced_roles()
        ]
        buckets = select_buckets(low_scored, floor=4.5, per_bucket=5)
        selected = [
            item for selection in buckets.values() for item in selection.roles
        ]

        self.assertEqual(len(selected), 15)
        self.assertEqual(buckets["Reach"].diagnostics.effective_floor, 3.5)

    def test_company_cap_relaxes_when_only_three_companies_exist(self):
        scored: list[ScoredJob] = []
        for index, item in enumerate(balanced_roles()):
            company = f"Only Co {index % 3}"
            scored.append(replace(item, job=replace(item.job, company=company)))

        buckets = select_buckets(scored, floor=5.8, per_bucket=5)
        selected = [
            item for selection in buckets.values() for item in selection.roles
        ]

        self.assertEqual(len(selected), 15)
        self.assertGreater(
            sum(
                selection.diagnostics.company_cap_relaxed_selected
                for selection in buckets.values()
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
