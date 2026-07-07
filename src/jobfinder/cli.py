from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .alerts import send_discord_notification
from .client import load_fixture
from .models import ScoredJob
from .reporting import CorrectionLog, ReportStats, build_report, select_buckets
from .scoring import is_internship_role, is_us_internship, is_us_location, score_job
from .sources import MultiCompanyClient, load_sources_config
from .state import load_state, save_state
from .tracker import ApplicationTracker, SUPPRESSED_STATUSES


ROOT = Path(__file__).resolve().parents[2]


def _select_alert_candidates(
    scored: list[ScoredJob],
    top_floor: float,
) -> list[ScoredJob]:
    """Use the same balanced recommendations for Discord and the report."""
    buckets = select_buckets(scored, top_floor, per_bucket=5)
    return [
        item
        for bucket in ("Reach", "Target", "Safe")
        for item in buckets[bucket].roles
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-finder",
        description="Run Bobby's multi-company opportunity intelligence agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Fetch, score, and report opportunities")
    run.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config" / "profile.json",
    )
    run.add_argument(
        "--sources",
        type=Path,
        default=ROOT / "config" / "sources.json",
    )
    run.add_argument("--fixture", type=Path)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument(
        "--show-history",
        action="store_true",
        help="Allow Applied, Rejected, and Not Interested jobs into recommendations",
    )
    tracker = sub.add_parser(
        "tracker",
        help="Open the persistent local application tracker",
    )
    tracker.add_argument("--host", default="127.0.0.1")
    tracker.add_argument("--port", type=int, default=8765)
    tracker.add_argument(
        "--data",
        type=Path,
        default=ROOT / "data" / "applications.json",
    )
    return parser


def run(
    config_path: Path,
    fixture: Path | None,
    dry_run: bool,
    sources_path: Path | None = None,
    show_history: bool = False,
) -> int:
    profile = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        if fixture:
            jobs = load_fixture(fixture)
            companies = {job.company for job in jobs}
            stats = ReportStats(
                companies_searched=len(companies),
                companies_succeeded=len(companies),
                raw_roles_found=len(jobs),
            )
        else:
            sources_config = load_sources_config(
                sources_path or ROOT / "config" / "sources.json"
            )
            result = MultiCompanyClient(max_workers=4).search(sources_config)
            jobs = result.roles
            stats = ReportStats(
                companies_searched=result.companies_attempted,
                companies_succeeded=result.companies_succeeded,
                raw_roles_found=result.raw_roles_found,
                source_failures=result.failures,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    non_us_excluded = sum(1 for job in jobs if not is_us_location(job))
    full_time_excluded = sum(
        1 for job in jobs if is_us_location(job) and not is_internship_role(job)
    )
    hard_filtered_jobs = [job for job in jobs if is_us_internship(job)]
    print(
        f"[FILTER] {len(hard_filtered_jobs)} explicit U.S. internships retained "
        f"from {len(jobs)} normalized roles"
    )

    state_path = ROOT / "data" / "state.json"
    tracker_path = ROOT / "data" / "applications.json"
    tracker = ApplicationTracker(tracker_path)
    state = load_state(state_path)
    seen = set(str(value) for value in state["seen_ids"])
    tracked_statuses = tracker.status_map()
    scored: list[ScoredJob] = []
    suppressed = 0
    duplicate_suppressed = 0
    pure_swe_excluded = 0
    for job in hard_filtered_jobs:
        score = score_job(job, profile)
        if (
            score.rejection_reason
            == "Pure SWE/backend/frontend/mobile/infrastructure role without clear AI-agentic scope"
        ):
            pure_swe_excluded += 1
        suppressing_record = tracker.suppression_match(job)
        status = tracked_statuses.get(job.tracking_id, "New")
        if suppressing_record:
            status = str(suppressing_record.get("status", status))
        if not show_history and (
            status in SUPPRESSED_STATUSES or suppressing_record is not None
        ):
            suppressed += 1
            if suppressing_record and suppressing_record.get("id") != job.tracking_id:
                duplicate_suppressed += 1
            continue
        scored.append(
            ScoredJob(
                job,
                score,
                is_new=status == "New" and job.tracking_id not in seen,
                tracking_status=status,
            )
        )
    if suppressed:
        print(
            f"[HISTORY] Suppressed {suppressed} Applied, Rejected, or "
            "Not Interested job(s). Use --show-history to include them."
        )
    top_floor = float(profile["thresholds"]["top_opportunity"])
    qualifying_counts = Counter(
        item.job.company
        for item in scored
        if item.score.relevant and item.score.overall >= top_floor
    )
    print(
        "[FILTER] qualifying internships by company: "
        + (
            ", ".join(
                f"{company}={count}"
                for company, count in qualifying_counts.most_common()
            )
            if qualifying_counts
            else "none"
        )
    )
    now = datetime.now(timezone.utc)
    thresholds = profile["thresholds"]
    per_bucket = int(thresholds.get("max_per_bucket", 5))
    tracked_reasons = [
        " - ".join(
            part
            for part in (
                str(record.get("reason_category", "")).strip(),
                str(record.get("manual_reason", "")).strip(),
            )
            if part
        )
        for status in ("Rejected", "Not Interested")
        for record in tracker.list_jobs(status)
    ]
    tracked_reasons = [reason for reason in tracked_reasons if reason]
    reason_counts = Counter(tracked_reasons)
    suggestions: list[str] = []
    reason_text = " ".join(tracked_reasons).lower()
    if "too pure swe" in reason_text:
        suggestions.append("Increase penalties for backend/frontend/mobile-only titles unless AI keywords are present.")
    if "not ai/agentic enough" in reason_text:
        suggestions.append("Require stronger LLM, agent, RAG, automation, evaluation, or AI-product evidence before selection.")
    if "too competitive" in reason_text:
        suggestions.append("Shift more Target/Safe slots toward smaller companies, applied AI tools, and analytics-adjacent AI roles.")
    if "bad location" in reason_text:
        suggestions.append("Down-rank repeated locations the user rejects unless the role is remote U.S.")
    correction_log = CorrectionLog(
        history_excluded=suppressed,
        duplicate_history_excluded=duplicate_suppressed,
        pure_swe_excluded=pure_swe_excluded,
        full_time_excluded=full_time_excluded,
        non_us_excluded=non_us_excluded,
        rejection_reasons=tuple(
            f"{reason} ({count})" for reason, count in reason_counts.most_common(8)
        ),
        suggestions=tuple(suggestions),
    )
    buckets = select_buckets(scored, top_floor, per_bucket=per_bucket)
    report = build_report(
        scored,
        profile,
        now,
        stats,
        bucket_selections=buckets,
        correction_log=correction_log,
    )

    if dry_run:
        print(report)
        return 0

    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    dated_path = report_dir / f"{now.date().isoformat()}.md"
    latest_path = report_dir / "latest.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path.write_text(report, encoding="utf-8")
    save_state(
        state_path,
        seen | {job.tracking_id for job in hard_filtered_jobs},
    )

    tracked_recommendations = [
        (item, bucket)
        for bucket in ("Reach", "Target", "Safe")
        for item in buckets[bucket].roles
    ]
    tracker.upsert_recommendations(tracked_recommendations)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)

    urgent_threshold = float(thresholds["urgent_apply"])
    alert_candidates = [item for item, _bucket in tracked_recommendations]
    print(f"[DEBUG] alert_candidates count = {len(alert_candidates)}")
    discord_sent = send_discord_notification(
        len(hard_filtered_jobs),
        alert_candidates,
        urgent_threshold,
    )
    if os.getenv("DISCORD_WEBHOOK_URL") and not discord_sent:
        print("WARNING: Discord notification was not sent. Check DISCORD_WEBHOOK_URL and alert candidate count.")
    urgent = [
        item
        for item in alert_candidates
        if item.score.overall >= urgent_threshold
    ]
    new_relevant = [
        item for item in alert_candidates if item.is_new
    ]
    print(
        f"Wrote {latest_path}. Reviewed {len(hard_filtered_jobs)} U.S. internships; "
        f"{len(new_relevant)} new relevant; {len(urgent)} urgent."
    )
    return 0


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run":
        raise SystemExit(
            run(
                args.config,
                args.fixture,
                args.dry_run,
                args.sources,
                args.show_history,
            )
        )
    if args.command == "tracker":
        from .web import serve_tracker

        serve_tracker(args.data, args.host, args.port)


if __name__ == "__main__":
    main()
