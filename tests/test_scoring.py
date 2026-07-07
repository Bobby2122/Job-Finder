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
        self.assertGreaterEqual(score.overall, 6.5)
        self.assertEqual(score.bucket, "Reach")

    def test_senior_phd_role_is_rejected_as_unrealistic(self):
        score = score_job(self.jobs["senior-1"], self.profile)
        self.assertFalse(score.relevant)
        self.assertTrue(score.rejection_reason)

    def test_phd_in_title_is_enough_to_reject(self):
        from dataclasses import replace

        phd_job = replace(
            self.jobs["ml-intern-1"],
            id="phd-title",
            title="Student Researcher (LLM - Seed) - 2027 Start (PhD)",
            requirement="Strong Python and machine learning research.",
        )
        score = score_job(phd_job, self.profile)
        self.assertIn("PhD/publication-heavy", score.rejection_reason)
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
        self.assertFalse(score.relevant)
        self.assertIn("Not an explicit internship", score.rejection_reason)

    def test_marketing_role_is_not_relevant(self):
        score = score_job(self.jobs["marketing-1"], self.profile)
        self.assertFalse(score.relevant)

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

    def test_2026_start_is_flagged_but_not_blocked_by_latest_filters(self):
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
        self.assertEqual(score.timing_fit, 1.0)
        self.assertTrue(score.relevant)
        self.assertIn("2026", score.concerns[-1])

    def test_full_time_analyst_is_excluded_even_when_relevant(self):
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
        self.assertFalse(score.relevant)
        self.assertIn("full-time", score.rejection_reason)

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
        self.assertIn("Not an explicit internship", score.rejection_reason)

    def test_remote_canada_internship_is_excluded(self):
        from dataclasses import replace

        canada = replace(
            self.jobs["ml-intern-1"],
            id="canada-intern",
            city="Remote Canada",
            country="Canada",
            location_path=("Remote Canada", "Canada"),
        )
        self.assertFalse(score_job(canada, self.profile).relevant)

    def test_full_us_state_name_is_accepted(self):
        from dataclasses import replace

        louisiana = replace(
            self.jobs["ml-intern-1"],
            id="louisiana-intern",
            city="Pineville, Louisiana",
            country="",
            location_path=("Pineville", "Louisiana"),
        )
        self.assertTrue(score_job(louisiana, self.profile).relevant)

    def test_bachelors_role_is_not_rejected_for_optional_phd_mention(self):
        from dataclasses import replace

        broad_degree_role = replace(
            self.jobs["ml-intern-1"],
            id="broad-degree-intern",
            title="Data Science Intern",
            requirement=(
                "Open to Undergrad, Master's, or PhD students. Python and "
                "statistics experience preferred."
            ),
        )
        score = score_job(broad_degree_role, self.profile)
        self.assertTrue(score.relevant)
        self.assertNotIn("PhD/publication-heavy", score.rejection_reason)

    def test_mba_only_internship_is_excluded(self):
        from dataclasses import replace

        mba_role = replace(
            self.jobs["ml-intern-1"],
            id="mba-only",
            title="MBA Intern - AI Operations",
            requirement="Candidates must be enrolled in an MBA program.",
        )
        score = score_job(mba_role, self.profile)
        self.assertFalse(score.relevant)
        self.assertIn("MBA or doctoral", score.rejection_reason)

    def test_missing_internship_type_and_ambiguous_title_is_excluded(self):
        from dataclasses import replace

        ambiguous = replace(
            self.jobs["ml-intern-1"],
            id="ambiguous",
            title="Data Analyst",
            recruitment_type="",
        )
        self.assertFalse(score_job(ambiguous, self.profile).relevant)

    def test_new_grad_role_is_excluded_even_with_intern_type(self):
        from dataclasses import replace

        new_grad = replace(
            self.jobs["ml-intern-1"],
            id="new-grad",
            title="Data Scientist New Grad",
            recruitment_type="Internship",
        )
        self.assertFalse(score_job(new_grad, self.profile).relevant)

    def test_return_offer_requires_explicit_internship_label(self):
        from dataclasses import replace

        return_offer = replace(
            self.jobs["ml-intern-1"],
            id="return-offer",
            title="Data Analyst Return Offer",
            recruitment_type="Internship",
        )
        self.assertFalse(score_job(return_offer, self.profile).relevant)

    def test_pure_swe_intern_without_ai_scope_is_rejected(self):
        from dataclasses import replace

        swe = replace(
            self.jobs["ml-intern-1"],
            id="pure-swe",
            title="Software Engineer Intern",
            description="Build backend services, frontend features, and mobile APIs.",
            requirement="Python or JavaScript experience preferred.",
        )
        score = score_job(swe, self.profile)
        self.assertFalse(score.relevant)
        self.assertTrue(score.pure_swe_signal)
        self.assertIn("Pure SWE", score.rejection_reason)

    def test_agentic_ai_intern_records_keywords_and_focus(self):
        from dataclasses import replace

        ai_role = replace(
            self.jobs["ml-intern-1"],
            id="agentic-ai",
            title="AI Agent Engineer Intern",
            description=(
                "Build LLM agents with RAG, tool calling, OpenAI API, "
                "embeddings, workflow automation, and model evaluation."
            ),
        )
        score = score_job(ai_role, self.profile)
        self.assertTrue(score.relevant)
        self.assertEqual(score.ai_focus, "AI-focused")
        self.assertIn("llm", score.ai_keywords)


if __name__ == "__main__":
    unittest.main()
