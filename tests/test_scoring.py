import json
import unittest
from pathlib import Path

from jobfinder.client import load_fixture
from jobfinder.reporting import build_report
from jobfinder.scoring import score_job


ROOT = Path(__file__).resolve().parents[1]


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "config" / "profile.json").read_text(encoding="utf-8")
        )
        cls.jobs = {
            job.id: job for job in load_fixture(ROOT / "tests/fixtures/jobs.json")
        }

    def test_ml_intern_is_high_quality_and_accessible(self):
        score = score_job(self.jobs["ml-intern-1"], self.profile)
        self.assertTrue(score.relevant)
        self.assertGreaterEqual(score.learning_value, 8.0)
        self.assertGreaterEqual(score.accessibility, 7.0)
        self.assertGreaterEqual(score.overall, 7.5)

    def test_senior_phd_role_is_rejected_as_unrealistic(self):
        score = score_job(self.jobs["senior-1"], self.profile)
        self.assertTrue(score.relevant)
        self.assertLessEqual(score.accessibility, 1.0)
        self.assertIn("PhD", score.rejection_reason)

    def test_phd_in_title_is_enough_to_reject(self):
        from dataclasses import replace

        phd_job = replace(
            self.jobs["ml-intern-1"],
            id="phd-title",
            title="Student Researcher (LLM - Seed) - 2027 Start (PhD)",
            requirement="Strong Python and machine learning research.",
        )
        score = score_job(phd_job, self.profile)
        self.assertIn("PhD", score.rejection_reason)
        self.assertEqual(score.competitiveness, "High")

    def test_non_early_career_role_is_not_a_top_match(self):
        from dataclasses import replace

        regular_job = replace(
            self.jobs["ml-intern-1"],
            id="regular-role",
            title="Machine Learning Engineer, AI Coding Tools",
            recruitment_type="Regular",
            requirement="Bachelor's degree. Strong Python and PyTorch.",
        )
        score = score_job(regular_job, self.profile)
        self.assertTrue(score.relevant)
        self.assertIn("new-grad accessibility", score.rejection_reason)

    def test_marketing_role_is_not_relevant(self):
        score = score_job(self.jobs["marketing-1"], self.profile)
        self.assertFalse(score.relevant)
        self.assertLess(score.skill_fit, 3.0)

    def test_report_has_required_sections(self):
        from datetime import datetime, timezone
        from jobfinder.models import ScoredJob

        scored = [
            ScoredJob(job, score_job(job, self.profile), is_new=True)
            for job in self.jobs.values()
        ]
        report = build_report(scored, self.profile, datetime.now(timezone.utc))
        self.assertIn("# Multi-Company Opportunity Intelligence Report", report)
        self.assertIn("## A. Reach Roles", report)
        self.assertIn("## B. Target Roles", report)
        self.assertIn("## C. Safe Roles", report)
        self.assertIn("## D. Rejected But Interesting", report)
        self.assertIn("## E. Strategy Advice", report)

    def test_china_role_is_excluded(self):
        from dataclasses import replace

        china_job = replace(
            self.jobs["ml-intern-1"],
            id="china-role",
            city="Beijing",
            country="China",
            location_path=("Beijing", "China"),
        )
        score = score_job(china_job, self.profile)
        self.assertFalse(score.geography_ok)
        self.assertFalse(score.relevant)

    def test_2026_start_is_rejected_even_if_description_mentions_2027(self):
        from dataclasses import replace

        old_timing = replace(
            self.jobs["ml-intern-1"],
            id="old-timing",
            title="Data Science Internship",
            description=(
                "This internship starts in Spring 2026. Program materials were "
                "updated for the 2027 recruiting calendar."
            ),
        )
        score = score_job(old_timing, self.profile)
        self.assertEqual(score.timing_fit, 2.0)
        self.assertIn("2026", score.rejection_reason)

    def test_entry_level_analyst_title_can_be_safe_without_intern_label(self):
        from dataclasses import replace

        analyst = replace(
            self.jobs["ml-intern-1"],
            id="risk-analyst",
            title="Risk Data Analyst",
            recruitment_type="Full time",
            description=(
                "Use Python, SQL, statistics, and forecasting to analyze insurance "
                "risk. Bachelor's degree required."
            ),
            requirement="Bachelor's degree required. Experience preferred.",
            company="Travelers",
            company_size_category="Insurance/risk",
            source_category="Insurance / reinsurance / risk analytics",
            role_family="Quant / Risk",
        )
        score = score_job(analyst, self.profile)
        self.assertTrue(score.relevant)
        self.assertFalse(score.rejection_reason)
        self.assertEqual(score.bucket, "Safe")

    def test_engineer_two_is_not_misread_as_entry_level_engineer_one(self):
        from dataclasses import replace

        engineer_two = replace(
            self.jobs["ml-intern-1"],
            id="engineer-two",
            title="Machine Learning Engineer II",
            recruitment_type="Regular",
            description="Build machine learning models and production data systems.",
            requirement="Production engineering experience required.",
        )
        score = score_job(engineer_two, self.profile)
        self.assertIn("new-grad accessibility", score.rejection_reason)


if __name__ == "__main__":
    unittest.main()
