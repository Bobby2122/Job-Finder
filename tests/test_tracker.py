from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jobfinder.client import load_fixture
from jobfinder.models import Role, ScoredJob, stable_job_id
from jobfinder.scoring import score_job
from jobfinder.state import (
    load_state,
    recommendation_state,
    save_state,
    update_state,
)
from jobfinder.tracker import ApplicationTracker


ROOT = Path(__file__).resolve().parents[1]


class TrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(
            (ROOT / "config/profile.json").read_text(encoding="utf-8")
        )
        cls.job = load_fixture(ROOT / "tests/fixtures/jobs.json")[0]
        cls.item = ScoredJob(
            cls.job,
            score_job(cls.job, cls.profile),
            is_new=True,
            tracking_status="New",
        )

    def test_stable_id_uses_normalized_identity_fields(self):
        first = stable_job_id(
            "Example Co",
            "Data Science Intern",
            "Atlanta, GA",
            "https://EXAMPLE.com/jobs/123/?source=campus",
        )
        second = stable_job_id(
            " example   co ",
            "data science intern",
            "ATLANTA, GA",
            "https://example.com/jobs/123",
        )
        changed = stable_job_id(
            "Example Co",
            "Data Science Intern",
            "Boston, MA",
            "https://example.com/jobs/123",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_status_and_notes_persist_across_tracker_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "applications.json"
            tracker = ApplicationTracker(path)
            tracker.upsert_recommendations([(self.item, "Target")])
            tracker.update_status(self.job.tracking_id, "Saved")
            tracker.update_notes(
                self.job.tracking_id,
                "Need cover letter and sponsorship answer.",
            )

            reloaded = ApplicationTracker(path)
            record = reloaded.get(self.job.tracking_id)
            self.assertIsNotNone(record)
            self.assertEqual(record["status"], "Saved")
            self.assertEqual(
                record["notes"],
                "Need cover letter and sponsorship answer.",
            )

    def test_opening_job_only_advances_new_to_viewed(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            tracker.upsert_recommendations([(self.item, "Target")])

            viewed = tracker.mark_viewed(self.job.tracking_id)
            self.assertEqual(viewed["status"], "Viewed")

            tracker.update_status(self.job.tracking_id, "Applied")
            still_applied = tracker.mark_viewed(self.job.tracking_id)
            self.assertEqual(still_applied["status"], "Applied")

    def test_pipeline_suppresses_applied_job_by_default(self):
        from jobfinder.cli import run

        config = ROOT / "config/profile.json"
        fixture = ROOT / "tests/fixtures/jobs.json"
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            with patch("jobfinder.cli.ROOT", run_root):
                with patch(
                    "jobfinder.cli.send_discord_notification",
                    return_value=True,
                ):
                    self.assertEqual(run(config, fixture, dry_run=False), 0)
                    tracker = ApplicationTracker(
                        run_root / "data/applications.json"
                    )
                    records = tracker.list_jobs()
                    self.assertEqual(len(records), 1)
                    tracker.update_status(records[0]["id"], "Applied")

                    self.assertEqual(run(config, fixture, dry_run=False), 0)
                    report = (
                        run_root / "reports/latest.md"
                    ).read_text(encoding="utf-8")
                    self.assertEqual(
                        run(
                            config,
                            fixture,
                            dry_run=False,
                            show_history=True,
                        ),
                        0,
                    )
                    history_report = (
                        run_root / "reports/latest.md"
                    ).read_text(encoding="utf-8")

        self.assertIn("- **Roles selected:** 0", report)
        self.assertIn("- **Roles selected:** 1", history_report)
        self.assertIn("- **Application status:** Applied", history_report)

    def test_pipeline_suppresses_previous_report_recommendation(self):
        from jobfinder.cli import run

        config = ROOT / "config/profile.json"
        fixture = ROOT / "tests/fixtures/jobs.json"
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            with patch("jobfinder.cli.ROOT", run_root):
                with patch(
                    "jobfinder.cli.send_discord_notification",
                    return_value=True,
                ):
                    self.assertEqual(run(config, fixture, dry_run=False), 0)
                    self.assertEqual(run(config, fixture, dry_run=False), 0)
                    report = (
                        run_root / "reports/latest.md"
                    ).read_text(encoding="utf-8")

        self.assertIn("- **Roles selected:** 1", report)
        self.assertIn("- **Application status:** PREVIOUSLY RECOMMENDED", report)

    def test_viewed_started_and_saved_do_not_suppress_future_recommendations(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            tracker.upsert_recommendations([(self.item, "Target")])

            tracker.update_status(self.job.tracking_id, "Viewed")
            self.assertIsNone(tracker.suppression_match(self.job, include_previous=True))

            tracker.update_status(self.job.tracking_id, "Started")
            self.assertIsNone(tracker.suppression_match(self.job, include_previous=True))

            tracker.update_status(self.job.tracking_id, "Saved")
            self.assertIsNone(tracker.suppression_match(self.job, include_previous=True))

    def test_viewing_one_company_role_does_not_suppress_different_role(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            tracker.add_manual_job(
                company="Analytics Co",
                title="Data Science Intern",
                location="Atlanta, GA",
                url="https://example.com/jobs/data-science",
                status="Viewed",
            )
            future = Role.normalized(
                id="future-product-analytics",
                company="Analytics Co",
                title="Product Analytics Intern",
                location="Atlanta, GA",
                employment_type="Internship",
                url="https://example.com/jobs/product-analytics",
                source="Official careers API",
                description="Experimentation, forecasting, and causal inference.",
                requirements="Undergraduate students preferred.",
            )

            self.assertIsNone(tracker.suppression_match(future, include_previous=True))

    def test_recent_new_previous_recommendation_is_cooldown_match(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            tracker.upsert_recommendations([(self.item, "Target")])

            match = tracker.suppression_match(self.job, include_previous=True)

        self.assertIsNotNone(match)

    def test_pipeline_suppresses_manual_applied_job_file(self):
        from jobfinder.cli import run

        config = ROOT / "config/profile.json"
        fixture = ROOT / "tests/fixtures/jobs.json"
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            tracker = ApplicationTracker(run_root / "data/applications.json")
            tracker.add_manual_job(
                company=self.job.company,
                title=self.job.title,
                location=self.job.location,
                url=self.job.url,
                status="applied",
                reason_category="already applied",
            )
            with patch("jobfinder.cli.ROOT", run_root):
                with patch(
                    "jobfinder.cli.send_discord_notification",
                    return_value=True,
                ):
                    self.assertEqual(run(config, fixture, dry_run=False), 0)
                    report = (
                        run_root / "reports/latest.md"
                    ).read_text(encoding="utf-8")

            manual = json.loads(
                (run_root / "data/manual_jobs.json").read_text(encoding="utf-8")
            )

        self.assertIn("- **Roles selected:** 0", report)
        self.assertEqual(
            manual["jobs"][self.job.tracking_id]["status"],
            "applied",
        )

    def test_pipeline_suppresses_rejected_job_file(self):
        from jobfinder.cli import run

        config = ROOT / "config/profile.json"
        fixture = ROOT / "tests/fixtures/jobs.json"
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            tracker = ApplicationTracker(run_root / "data/applications.json")
            tracker.add_manual_job(
                company=self.job.company,
                title=self.job.title,
                location=self.job.location,
                url=self.job.url,
                status="rejected",
                reason_category="too SWE",
            )
            with patch("jobfinder.cli.ROOT", run_root):
                with patch(
                    "jobfinder.cli.send_discord_notification",
                    return_value=True,
                ):
                    self.assertEqual(run(config, fixture, dry_run=False), 0)
                    report = (
                        run_root / "reports/latest.md"
                    ).read_text(encoding="utf-8")

        self.assertIn("- **Roles selected:** 0", report)
        self.assertIn("- too SWE (1)", report)

    def test_current_run_duplicate_company_title_jobs_are_filtered(self):
        from jobfinder.cli import _deduplicate_scored

        first = self.item
        duplicate_role = Role.normalized(
            id="same-direction",
            company=self.job.company,
            title="AI Agent Engineer Internship",
            location="Seattle, WA",
            employment_type="Internship",
            url="https://example.com/same-direction",
            source="Official careers API",
            description=(
                "Build LLM agents with RAG, embeddings, OpenAI API, "
                "workflow automation, and model evaluation."
            ),
            requirements="Undergraduate students preferred.",
            role_family=self.job.role_family,
            company_size_category=self.job.company_size_category,
            source_category=self.job.source_category,
        )
        duplicate = ScoredJob(
            duplicate_role,
            score_job(duplicate_role, self.profile),
            is_new=True,
            tracking_status="New",
        )

        deduped, excluded = _deduplicate_scored([first, duplicate])

        self.assertEqual(excluded, 1)
        self.assertEqual(len(deduped), 1)

    def test_phase_one_json_files_are_written_and_shareable(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            tracker = ApplicationTracker(data_dir / "applications.json")
            tracker.upsert_recommendations([(self.item, "Target")])
            tracker.update_status(
                self.job.tracking_id,
                "Rejected",
                reason_category="not AI focused",
            )
            tracker.add_manual_job(
                company="Manual AI Co",
                title="AI Engineer Intern",
                location="Remote, United States",
                url="https://example.com/manual-ai",
                status="saved",
            )

            history = json.loads(
                (data_dir / "job_history.json").read_text(encoding="utf-8")
            )
            manual = json.loads(
                (data_dir / "manual_jobs.json").read_text(encoding="utf-8")
            )
            feedback = json.loads(
                (data_dir / "user_feedback.json").read_text(encoding="utf-8")
            )

        self.assertIn(self.job.tracking_id, history["jobs"])
        self.assertIn("normalized_title", history["jobs"][self.job.tracking_id])
        self.assertEqual(history["jobs"][self.job.tracking_id]["status"], "rejected")
        self.assertEqual(len(manual["jobs"]), 1)
        self.assertEqual(feedback["feedback"][0]["reason"], "not AI focused")

    def test_viewed_status_is_preserved_when_job_is_seen_again(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            tracker.upsert_recommendations([(self.item, "Target")])
            tracker.mark_viewed(self.job.tracking_id)
            later_item = replace(
                self.item,
                tracking_status="Viewed",
                is_new=False,
            )
            tracker.upsert_recommendations([(later_item, "Reach")])
            record = tracker.get(self.job.tracking_id)

        self.assertEqual(record["status"], "Viewed")
        self.assertEqual(record["bucket"], "Reach")

    def test_manual_applied_similar_job_suppresses_future_recommendation(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            record = tracker.add_manual_job(
                company=self.job.company,
                title="Machine Learning Research Internship",
                location=self.job.location,
                url=f"{self.job.url}?utm_source=manual",
                status="Applied",
                reason_category="already applied",
                manual_reason="Applied from company site.",
            )

            match = tracker.suppression_match(self.job)

        self.assertIsNotNone(match)
        self.assertEqual(match["id"], record["id"])
        self.assertEqual(match["status"], "Applied")
        self.assertEqual(match["reason_category"], "already applied")

    def test_dismissed_alias_is_stored_as_not_interested(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            record = tracker.add_manual_job(
                company="Example AI",
                title="AI Automation Intern",
                location="Remote, United States",
                url="https://example.com/jobs/ai-automation",
                status="dismissed",
                reason_category="not AI/agentic enough",
            )

        self.assertEqual(record["status"], "Not Interested")

    def test_state_first_discovered_but_not_recommended(self):
        state = update_state(
            load_state(Path("/missing/state.json")),
            discovered_ids=["job_a"],
            recommended_ids=[],
        )
        self.assertIn("job_a", state["discovered_ids"])
        self.assertNotIn("job_a", state["recommended_ids"])
        self.assertEqual(recommendation_state(state, "job_b"), "New")

    def test_state_second_run_first_recommended_is_newly_qualified(self):
        state = update_state(
            load_state(Path("/missing/state.json")),
            discovered_ids=["job_a"],
            recommended_ids=[],
        )
        self.assertEqual(
            recommendation_state(state, "job_a"),
            "NEWLY QUALIFIED",
        )
        updated = update_state(
            state,
            discovered_ids=["job_a"],
            recommended_ids=["job_a"],
        )
        self.assertIn("job_a", updated["recommended_ids"])

    def test_state_previously_recommended(self):
        state = update_state(
            load_state(Path("/missing/state.json")),
            discovered_ids=["job_a"],
            recommended_ids=["job_a"],
        )
        self.assertEqual(
            recommendation_state(state, "job_a"),
            "PREVIOUSLY RECOMMENDED",
        )

    def test_state_tracker_applied_status_wins_over_new_badges(self):
        state = update_state(
            load_state(Path("/missing/state.json")),
            discovered_ids=["job_a"],
            recommended_ids=[],
        )
        self.assertEqual(
            recommendation_state(state, "job_a", "Applied"),
            "APPLIED",
        )

    def test_legacy_seen_ids_state_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"seen_ids": ["job_a"]}),
                encoding="utf-8",
            )
            state = load_state(path)
            save_state(path, state)
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertIn("job_a", state["discovered_ids"])
        self.assertIn("job_a", state["recommended_ids"])
        self.assertNotIn("seen_ids", persisted)

    def test_started_status_is_preserved_when_recommended_again(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = ApplicationTracker(Path(directory) / "applications.json")
            tracker.upsert_recommendations([(self.item, "Target")])
            tracker.update_status(self.job.tracking_id, "Started")
            later_item = replace(
                self.item,
                tracking_status="Started",
                is_new=False,
            )
            tracker.upsert_recommendations([(later_item, "Reach")])
            record = tracker.get(self.job.tracking_id)

        self.assertEqual(record["status"], "Started")
        self.assertEqual(record["bucket"], "Reach")


if __name__ == "__main__":
    unittest.main()
