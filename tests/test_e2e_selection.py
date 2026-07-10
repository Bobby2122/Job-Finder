from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobfinder.client import load_fixture
from jobfinder.cli import run
from jobfinder.models import normalize_company_name, normalize_job_title
from jobfinder.tracker import ApplicationTracker


ROOT = Path(__file__).resolve().parents[1]


def _selected_report_blocks(report: str) -> list[str]:
    selected_part = report.split("## D. Rejected But Interesting", 1)[0]
    return re.findall(r"^### \[(.*?)\]\((.*?)\)", selected_part, flags=re.M)


class EndToEndSelectionTests(unittest.TestCase):
    def test_end_to_end_fixture_produces_exact_5_5_5(self):
        fixture = ROOT / "tests/fixtures/e2e_5_5_5_roles.json"
        jobs = {job.id: job for job in load_fixture(fixture)}
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            tracker = ApplicationTracker(run_root / "data/applications.json")
            for job_id, status in (
                ("applied-1", "Applied"),
                ("rejected-1", "Rejected"),
                ("not-interested-1", "Not Interested"),
                ("previous-1", "New"),
                ("viewed-1", "Viewed"),
                ("saved-1", "Saved"),
            ):
                job = jobs[job_id]
                tracker.add_manual_job(
                    company=job.company,
                    title=job.title,
                    location=job.location,
                    url=job.url,
                    status=status,
                )

            with patch("jobfinder.cli.ROOT", run_root), patch(
                "jobfinder.cli.send_discord_notification",
                return_value=True,
            ):
                self.assertEqual(
                    run(ROOT / "config/profile.json", fixture, dry_run=False),
                    0,
                )

            report_path = run_root / "reports/latest.md"
            report = report_path.read_text(encoding="utf-8")
            selected = _selected_report_blocks(report)
            history = json.loads(
                (run_root / "data/job_history.json").read_text(encoding="utf-8")
            )
            records = list(history["jobs"].values())

        self.assertIn("- **Roles selected:** 15", report)
        self.assertIn("## A. Reach Roles (5)", report)
        self.assertIn("## B. Target Roles (5)", report)
        self.assertIn("## C. Safe Roles (5)", report)
        self.assertEqual(len(selected), 15)
        self.assertEqual(len(records), 15)
        company_counts: dict[str, int] = {}
        identities: set[tuple[str, str]] = set()
        for record in records:
            company = str(record["company"])
            company_counts[company] = company_counts.get(company, 0) + 1
            identities.add(
                (
                    normalize_company_name(company),
                    normalize_job_title(str(record["title"])),
                )
            )
            self.assertIn("United States", str(record["location"]))
            self.assertNotIn(record["status"], {"applied", "rejected", "not_interested"})
            self.assertTrue(str(record["application_url"]).startswith("https://"))
            self.assertTrue(str(record["id"]).startswith("job_"))
        self.assertTrue(all(count <= 2 for count in company_counts.values()))
        self.assertEqual(len(identities), 15)
        self.assertTrue(any(record["status"] == "saved" for record in records))
        self.assertRegex(
            report,
            r"- \*\*Selected company-size mix:\*\* Large 5 \| Mid [45] \| Small/startup [56]",
        )
        if "Mid 5 | Small/startup 5" not in report:
            self.assertIn("Filled a missing size slot", report)

    def test_shortage_fixtures_render_counter_based_explanations(self):
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            fixture = ROOT / "tests/fixtures/e2e_shortage_reach.json"
            jobs = {job.id: job for job in load_fixture(fixture)}
            tracker = ApplicationTracker(run_root / "data/applications.json")
            previous = jobs["previous-1"]
            tracker.add_manual_job(
                company=previous.company,
                title=previous.title,
                location=previous.location,
                url=previous.url,
                status="New",
            )
            with patch("jobfinder.cli.ROOT", run_root), patch(
                "jobfinder.cli.send_discord_notification",
                return_value=True,
            ):
                self.assertEqual(
                    run(ROOT / "config/profile.json", fixture, dry_run=False),
                    0,
                )
            reach_report = (run_root / "reports/latest.md").read_text(encoding="utf-8")

        self.assertIn("Reach: selected 3 of 5", reach_report)
        self.assertIn("timing hard reject", reach_report)
        self.assertIn("tracker/history suppression", reach_report)

        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            with patch("jobfinder.cli.ROOT", run_root), patch(
                "jobfinder.cli.send_discord_notification",
                return_value=True,
            ):
                self.assertEqual(
                    run(
                        ROOT / "config/profile.json",
                        ROOT / "tests/fixtures/e2e_shortage_company_cap.json",
                        dry_run=False,
                    ),
                    0,
                )
            cap_report = (run_root / "reports/latest.md").read_text(encoding="utf-8")
        self.assertIn("two-role-per-company cap", cap_report)

        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)
            with patch("jobfinder.cli.ROOT", run_root), patch(
                "jobfinder.cli.send_discord_notification",
                return_value=True,
            ):
                self.assertEqual(
                    run(
                        ROOT / "config/profile.json",
                        ROOT / "tests/fixtures/e2e_shortage_low_relevance.json",
                        dry_run=False,
                    ),
                    0,
                )
            low_report = (run_root / "reports/latest.md").read_text(encoding="utf-8")
        self.assertIn("low career-relevance rejection", low_report)


if __name__ == "__main__":
    unittest.main()
