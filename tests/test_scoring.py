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
        self.assertIn("## A. Top Opportunities", report)
        self.assertIn("## B. Rejected But Interesting", report)
        self.assertIn("## C. Strategy Advice", report)
        self.assertIn("Why it might not be a good idea", report)


if __name__ == "__main__":
    unittest.main()
