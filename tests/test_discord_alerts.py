import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from jobfinder.alerts import (
    build_discord_message,
    send_discord_notification,
    send_discord_webhook,
)
from jobfinder.client import load_fixture
from jobfinder.models import ScoredJob
from jobfinder.scoring import score_job


ROOT = Path(__file__).resolve().parents[1]


class DiscordAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        profile = json.loads(
            (ROOT / "config/profile.json").read_text(encoding="utf-8")
        )
        job = load_fixture(ROOT / "tests/fixtures/jobs.json")[0]
        cls.item = ScoredJob(job, score_job(job, profile), is_new=True)

    def test_message_contains_urgent_details_and_daily_summary(self):
        message = build_discord_message(401, [self.item], [self.item])
        self.assertIn("🚨 Urgent Internship Match Found", message)
        self.assertIn("Internship Intelligence Report", message)
        self.assertIn("Jobs reviewed: **401**", message)
        self.assertIn("Top matches: **1**", message)
        self.assertIn("Urgent matches: **1**", message)
        self.assertIn(self.item.job.title, message)
        self.assertIn("Skill ", message)
        self.assertIn("Learning ", message)
        self.assertIn("Accessibility ", message)
        self.assertIn("Competitiveness:", message)
        self.assertIn("Why it fits Bobby's math + ML background:", message)
        self.assertLessEqual(len(message), 2000)

    @patch.dict(
        os.environ,
        {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"},
        clear=False,
    )
    @patch("jobfinder.alerts.urlopen")
    def test_webhook_posts_discord_content_payload(self, urlopen):
        response = MagicMock()
        response.status = 204
        urlopen.return_value.__enter__.return_value = response

        self.assertTrue(send_discord_webhook("Hello Discord"))

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {"content": "Hello Discord"})
        self.assertEqual(request.full_url, "https://discord.com/api/webhooks/123/token")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 15)

    @patch.dict(os.environ, {}, clear=True)
    @patch("jobfinder.alerts.urlopen")
    def test_missing_webhook_logs_clear_warning(self, urlopen):
        with patch("builtins.print") as logged:
            self.assertFalse(send_discord_webhook("Hello Discord"))
        urlopen.assert_not_called()
        logged.assert_called_once_with(
            "Discord notification skipped: DISCORD_WEBHOOK_URL is not set."
        )

    @patch.dict(
        os.environ,
        {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"},
        clear=False,
    )
    @patch("jobfinder.alerts.urlopen", side_effect=URLError("offline"))
    def test_request_failure_is_logged_without_raising(self, _urlopen):
        with patch("builtins.print") as logged:
            self.assertFalse(send_discord_webhook("Hello Discord"))
        logged.assert_called_once_with("Discord notification failed: URLError")

    @patch("jobfinder.alerts.send_discord_webhook")
    def test_notification_uses_urgent_format(self, webhook):
        webhook.return_value = True
        self.assertTrue(
            send_discord_notification(
                401,
                [self.item],
                8.5,
            )
        )
        message = webhook.call_args.args[0]
        self.assertIn("🚨 Urgent Internship Match Found", message)

    @patch("jobfinder.alerts.send_discord_webhook")
    def test_notification_uses_daily_format_without_urgent_job(self, webhook):
        webhook.return_value = True
        non_urgent = replace(
            self.item,
            score=replace(self.item.score, overall=8.0),
        )
        self.assertTrue(
            send_discord_notification(
                401,
                [non_urgent],
                8.5,
            )
        )
        message = webhook.call_args.args[0]
        self.assertNotIn("🚨 Urgent Internship Match Found", message)
        self.assertIn("Internship Intelligence Report", message)

    @patch("jobfinder.alerts.send_discord_webhook")
    def test_notification_skips_when_no_relevant_jobs(self, webhook):
        self.assertFalse(
            send_discord_notification(
                401,
                [],
                8.5,
            )
        )
        webhook.assert_not_called()

    def test_main_pipeline_invokes_discord_after_report_generation(self):
        from jobfinder.cli import run

        config = ROOT / "config/profile.json"
        fixture = ROOT / "tests/fixtures/jobs.json"
        with tempfile.TemporaryDirectory() as directory:
            run_root = Path(directory)

            def verify_report_exists(*_args):
                self.assertTrue((run_root / "reports/latest.md").exists())
                return True

            with patch("jobfinder.cli.ROOT", run_root):
                with patch(
                    "jobfinder.cli.send_discord_notification",
                    side_effect=verify_report_exists,
                ) as notification:
                    self.assertEqual(run(config, fixture, dry_run=False), 0)

        notification.assert_called_once()
        reviewed_count, candidates, threshold = notification.call_args.args
        self.assertEqual(reviewed_count, 3)
        self.assertTrue(candidates)
        self.assertEqual(threshold, 8.5)

    def test_rejection_reason_does_not_exclude_alert_candidate(self):
        from jobfinder.cli import _select_alert_candidates

        candidate_with_note = replace(
            self.item,
            score=replace(
                self.item.score,
                relevant=True,
                overall=8.8,
                rejection_reason="Timeline mismatch noted for the report",
            ),
        )

        selected = _select_alert_candidates([candidate_with_note], top_floor=6.2)

        self.assertEqual(selected, [candidate_with_note])


if __name__ == "__main__":
    unittest.main()
