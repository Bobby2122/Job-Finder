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

    def test_ai_agent_intern_is_high_quality_and_accessible(self):
        score = score_job(self.jobs["ml-intern-1"], self.profile)
        self.assertTrue(score.relevant)
        self.assertTrue(score.ai_engineer)
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
            title="Research Engineer (AI) Intern - 2027 Start (PhD)",
            requirement="Strong Python, LLM, RAG, and machine learning research.",
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

    def test_2026_start_is_blocked_by_current_ai_internship_filters(self):
        from dataclasses import replace

        old_timing = replace(
            self.jobs["ml-intern-1"],
            id="old-timing",
            title="AI Agent Engineer Internship",
            description=(
                "This internship starts in Spring 2026. Build LLM agents with "
                "RAG, embeddings, and model evaluation."
            ),
        )
        score = score_job(old_timing, self.profile)
        self.assertEqual(score.timing_fit, 1.0)
        self.assertFalse(score.relevant)
        self.assertIn("2026", score.rejection_reason)

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

    def test_operations_research_optimization_intern_is_relevant(self):
        from dataclasses import replace

        or_role = replace(
            self.jobs["ml-intern-1"],
            id="or-intern",
            title="Operations Research Optimization Intern - Summer 2027",
            description=(
                "Build decision models using linear programming, integer "
                "programming, stochastic optimization, simulation, and forecasting."
            ),
            requirement="Open to mathematics, applied mathematics, statistics, or operations research majors.",
        )
        score = score_job(or_role, self.profile)
        self.assertTrue(score.relevant)
        self.assertEqual(score.primary_track, "Operations Research / Optimization")
        self.assertGreaterEqual(score.optimization_relevance_score, 15)

    def test_applied_math_scientific_computing_intern_is_relevant(self):
        from dataclasses import replace

        math_role = replace(
            self.jobs["ml-intern-1"],
            id="applied-math",
            title="Scientific Computing Intern - Summer 2027",
            description=(
                "Use numerical methods, PDE and ODE models, dynamical systems, "
                "simulation, scientific computing, and mathematical modeling."
            ),
            requirement="Applied mathematics or computational science background preferred.",
        )
        score = score_job(math_role, self.profile)
        self.assertTrue(score.relevant)
        self.assertEqual(score.primary_track, "Applied Math / Computational Math")
        self.assertGreaterEqual(score.applied_math_relevance_score, 15)

    def test_applied_math_analytics_signals_are_relevant(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="network-planning",
            title="Network Planning Analytics Intern - Summer 2027",
            description=(
                "Build forecasting, decision support, supply chain analytics, "
                "pricing analytics, and operations analytics models."
            ),
            requirement="Open to mathematics, economics, statistics, or operations research majors.",
        )
        score = score_job(job, self.profile)
        self.assertTrue(score.relevant)
        self.assertGreaterEqual(score.optimization_relevance_score, 10)

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
        self.assertIn("MBA-only", score.rejection_reason)

    def test_mba_preferred_with_bachelors_path_is_kept(self):
        from dataclasses import replace

        mba_preferred = replace(
            self.jobs["ml-intern-1"],
            id="mba-preferred",
            title="Data Science Intern - Strategy Analytics",
            description=(
                "Use experimentation, forecasting, causal inference, and "
                "statistical modeling for product analytics."
            ),
            requirement=(
                "Currently pursuing a bachelor's degree in math, statistics, "
                "computer science, economics, or related field. MBA preferred."
            ),
        )
        score = score_job(mba_preferred, self.profile)
        self.assertTrue(score.relevant)
        self.assertEqual(score.degree_status, "eligible")

    def test_active_clearance_required_is_rejected(self):
        from dataclasses import replace

        cleared = replace(
            self.jobs["ml-intern-1"],
            id="active-clearance",
            title="Operations Research Intern - Summer 2027",
            description="Build simulation and optimization models.",
            requirement="Candidates must have an active Secret clearance.",
        )
        score = score_job(cleared, self.profile)
        self.assertFalse(score.relevant)
        self.assertEqual(score.work_authorization_status, "blocked")
        self.assertIn("active/current security clearance", score.rejection_reason)

    def test_clearance_eligible_and_citizenship_required_is_retained(self):
        from dataclasses import replace

        eligible = replace(
            self.jobs["ml-intern-1"],
            id="clearance-eligible",
            title="Modeling and Simulation Intern - Summer 2027",
            description="Use numerical methods, simulation, and decision models.",
            requirement=(
                "U.S. citizenship is required. Must be able to obtain a security "
                "clearance."
            ),
        )
        score = score_job(eligible, self.profile)
        self.assertTrue(score.relevant)
        self.assertEqual(score.work_authorization_status, "concern")
        self.assertIn("clearance", " ".join(score.concerns).lower())

    def test_masters_only_description_is_rejected(self):
        from dataclasses import replace

        masters = replace(
            self.jobs["ml-intern-1"],
            id="masters-only",
            title="Data Science Intern",
            description="Forecasting and statistical modeling internship.",
            requirement="Candidates must be enrolled in a master's program.",
        )
        score = score_job(masters, self.profile)
        self.assertFalse(score.relevant)
        self.assertIn("Master", score.rejection_reason)

    def test_missing_description_plausible_title_is_manual_review_not_hard_reject(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="missing-description",
            title="Data Science Intern - Summer 2027",
            description="",
            requirement="",
        )
        score = score_job(job, self.profile)
        self.assertTrue(score.relevant)
        self.assertIn("manual review", " ".join(score.concerns).lower())


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

    def test_pure_swe_intern_without_ai_scope_is_penalized_by_relevance(self):
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
        self.assertLess(score.overall, 4.5)
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
        self.assertTrue(score.ai_engineer)
        self.assertEqual(score.ai_focus, "AI Engineer / Agentic AI")
        self.assertIn("llm", score.ai_keywords)

    def test_single_rag_keyword_does_not_make_unrelated_role_ai_engineer(self):
        from dataclasses import replace

        project = replace(
            self.jobs["ml-intern-1"],
            id="pm-rag",
            title="Project Management Intern",
            description="Coordinate real estate facilities projects. The page mentions RAG once in boilerplate.",
            requirement="Strong communication and project tracking skills.",
        )
        score = score_job(project, self.profile)
        self.assertFalse(score.ai_engineer)
        self.assertFalse(score.relevant)
        self.assertIn("Insufficient relevance", score.rejection_reason)

    def test_unspecified_start_date_is_tier_b_not_rejected(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="unspecified-start",
            title="AI Agent Engineer Intern",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.timing_tier, "B")
        self.assertTrue(score.relevant)
        self.assertIn("Start date not stated", score.timing_reason)

    def test_summer_2027_is_tier_b_and_kept(self):
        from dataclasses import replace

        summer = replace(
            self.jobs["ml-intern-1"],
            id="summer-2027",
            title="Machine Learning Intern - Summer 2027",
        )
        score = score_job(summer, self.profile)
        self.assertEqual(score.timing_tier, "B")
        self.assertTrue(score.relevant)

    def test_fall_2026_is_tier_c_not_hard_rejected(self):
        from dataclasses import replace

        fall = replace(
            self.jobs["ml-intern-1"],
            id="fall-2026",
            title="AI Intern - Fall 2026",
            description="Build LLM agents and model evaluation systems.",
        )
        score = score_job(fall, self.profile)
        self.assertEqual(score.timing_tier, "C")
        self.assertTrue(score.relevant)

    def test_fall_2027_is_hard_rejected(self):
        from dataclasses import replace

        fall = replace(
            self.jobs["ml-intern-1"],
            id="fall-2027",
            title="AI Intern - Fall 2027",
        )
        score = score_job(fall, self.profile)
        self.assertEqual(score.timing_tier, "Hard reject")
        self.assertFalse(score.relevant)

    def test_rolling_off_cycle_and_six_month_timing_are_tier_b(self):
        from dataclasses import replace

        variants = [
            "Rolling internship with flexible start date",
            "Off-cycle internship for 3-6 month projects",
            "Six month co-op building AI systems",
        ]
        for index, description in enumerate(variants):
            job = replace(
                self.jobs["ml-intern-1"],
                id=f"timing-flex-{index}",
                title="AI Agent Engineer Intern",
                description=description + " with LLM model evaluation.",
            )
            self.assertEqual(score_job(job, self.profile).timing_tier, "B")

    def test_software_engineer_ml_scope_is_not_pure_swe(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="swe-ml",
            title="Software Engineer Intern, Machine Learning",
            description="Build and evaluate machine learning models, train pipelines, and deploy model evaluation tools.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "ai_ml_engineering")
        self.assertTrue(score.relevant)

    def test_software_engineer_ai_platform_is_kept(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="swe-ai-platform",
            title="Software Engineer Intern, AI Platform",
            description="Develop AI platform services for LLM evaluation, RAG workflows, embeddings, and deployment.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "ai_ml_engineering")
        self.assertTrue(score.relevant)

    def test_software_engineer_optimization_is_kept(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="swe-optimization",
            title="Software Engineer Intern, Optimization",
            description="Build optimization algorithms, simulation tools, forecasting models, and decision science systems.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "optimization_modeling")
        self.assertTrue(score.relevant)

    def test_software_engineer_robotics_is_kept(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="swe-robotics",
            title="Software Engineer Intern, Robotics",
            description="Develop robotics autonomy software with computer vision, simulation, and autonomous systems experiments.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "optimization_modeling")
        self.assertTrue(score.relevant)

    def test_frontend_role_is_pure_swe(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="frontend",
            title="Frontend Software Engineer Intern",
            description="Build frontend web UI, React components, mobile interfaces, and backend CRUD APIs.",
            requirement="JavaScript and CSS.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "pure_swe")
        self.assertFalse(score.relevant)

    def test_data_engineering_for_ml_is_kept(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="data-eng-ml",
            title="Data Engineer Intern",
            description="Build data pipelines for ML platform training data, feature store workflows, and experimentation.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "data_engineering_for_ml")
        self.assertTrue(score.relevant)

    def test_ambiguous_software_role_is_uncertain_not_pure_swe(self):
        from dataclasses import replace

        job = replace(
            self.jobs["ml-intern-1"],
            id="ambiguous-swe",
            title="Software Engineer Intern",
            description="Build internal tools and collaborate with product teams.",
            requirement="Python experience preferred.",
        )
        score = score_job(job, self.profile)
        self.assertEqual(score.role_classification, "uncertain")
        self.assertFalse(score.pure_swe_signal)


if __name__ == "__main__":
    unittest.main()
