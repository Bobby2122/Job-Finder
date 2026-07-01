from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .alerts import send_discord_notification
from .client import ByteDanceClient, ByteDanceClientError, load_fixture
from .models import ScoredJob
from .reporting import build_report
from .scoring import score_job
from .state import load_state, save_state


ROOT = Path(__file__).resolve().parents[2]


def _select_alert_candidates(
    scored: list[ScoredJob],
    top_floor: float,
) -> list[ScoredJob]:
    """Keep alert selection inclusive; rejection notes are report context only."""
    return [
        item
        for item in scored
        if item.score.relevant and item.score.overall >= top_floor
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-finder",
        description="Run Bobby's ByteDance opportunity intelligence agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Fetch, score, and report opportunities")
    run.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "profile.json",
    )
    run.add_argument("--fixture", type=Path)
    run.add_argument("--dry-run", action="store_true")
    return parser


def run(config_path: Path, fixture: Path | None, dry_run: bool) -> int:
    profile = json.loads(config_path.read_text(encoding="utf-8"))
    search = profile["search"]
    try:
        if fixture:
            jobs = load_fixture(fixture)
        else:
            jobs = ByteDanceClient().search(
                search["keywords"],
                int(search["page_size"]),
                int(search["max_pages_per_keyword"]),
            )
    except (ByteDanceClientError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    state_path = ROOT / "data" / "state.json"
    state = load_state(state_path)
    seen = set(str(value) for value in state["seen_ids"])
    scored = [
        ScoredJob(job, score_job(job, profile), is_new=job.id not in seen)
        for job in jobs
    ]
    now = datetime.now(timezone.utc)
    report = build_report(scored, profile, now)
    thresholds = profile["thresholds"]

    if dry_run:
        print(report)
        return 0

    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    dated_path = report_dir / f"{now.date().isoformat()}.md"
    latest_path = report_dir / "latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    save_state(state_path, seen | {job.id for job in jobs})

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)

    urgent_threshold = float(thresholds["urgent_apply"])
    top_floor = float(thresholds["top_opportunity"])
    alert_candidates = _select_alert_candidates(scored, top_floor)
    print(f"[DEBUG] alert_candidates count = {len(alert_candidates)}")
    send_discord_notification(
        len(jobs),
        alert_candidates,
        urgent_threshold,
    )
    urgent = [
        item
        for item in alert_candidates
        if item.score.overall >= urgent_threshold
    ]
    new_relevant = [
        item for item in alert_candidates if item.is_new
    ]
    print(
        f"Wrote {latest_path}. Reviewed {len(jobs)} roles; "
        f"{len(new_relevant)} new relevant; {len(urgent)} urgent."
    )
    return 0


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run":
        raise SystemExit(run(args.config, args.fixture, args.dry_run))


if __name__ == "__main__":
    main()
