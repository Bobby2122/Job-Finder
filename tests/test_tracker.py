from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jobfinder.client import load_fixture
from jobfinder.models import ScoredJob, stable_job_id
from jobfinder.scoring import score_job
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

        self.assertIn("- **Roles selected:** 0", report)
        self.assertIn(
            "- **Excluded because duplicate or seen in a previous report:** 1",
            report,
        )

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


if __name__ == "__main__":
    unittest.main()
