from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .alerts import send_discord_notification
from .client import load_fixture
from .models import (
    ScoredJob,
    normalize_application_url,
    normalize_company_name,
    normalize_job_title,
    normalize_location_name,
    similar_job_titles,
)
from .reporting import CorrectionLog, ReportStats, build_report, select_buckets
from .scoring import (
    classify_ai_engineer,
    is_internship_role,
    is_pure_swe_title,
    is_target_timing,
    is_us_internship,
    is_us_location,
    score_job,
)
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


def _same_direction(first: ScoredJob, second: ScoredJob) -> bool:
    if normalize_company_name(first.job.company) != normalize_company_name(
        second.job.company
    ):
        return False
    if normalize_application_url(first.job.url) == normalize_application_url(
        second.job.url
    ):
        return True
    if (
        normalize_job_title(first.job.title) == normalize_job_title(second.job.title)
        and normalize_location_name(first.job.location)
        == normalize_location_name(second.job.location)
    ):
        return True
    if similar_job_titles(first.job.title, second.job.title):
        return True
    if (
        first.job.role_family
        and second.job.role_family
        and first.job.role_family == second.job.role_family
        and similar_job_titles(first.job.title, second.job.title)
    ):
        return True
    return False


def _deduplicate_scored(scored: list[ScoredJob]) -> tuple[list[ScoredJob], int]:
    selected: list[ScoredJob] = []
    excluded = 0
    for item in sorted(scored, key=lambda entry: entry.score.overall, reverse=True):
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(selected)
                if _same_direction(existing, item)
            ),
            None,
        )
        if duplicate_index is None:
            selected.append(item)
            continue
        excluded += 1
        existing = selected[duplicate_index]
        if item.score.overall > existing.score.overall:
            selected[duplicate_index] = item
    return selected, excluded


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

    not_ai_excluded = 0
    pure_swe_excluded = 0
    ai_candidates = []
    for job in jobs:
        classification = classify_ai_engineer(job)
        if not classification.is_ai_engineer:
            not_ai_excluded += 1
            if classification.pure_swe_signal or is_pure_swe_title(job):
                pure_swe_excluded += 1
            continue
        ai_candidates.append(job)

    non_us_excluded = sum(1 for job in ai_candidates if not is_us_location(job))
    full_time_excluded = sum(
        1 for job in ai_candidates if is_us_location(job) and not is_internship_role(job)
    )
    wrong_date_excluded = sum(
        1
        for job in ai_candidates
        if is_us_location(job)
        and is_internship_role(job)
        and not is_target_timing(job, profile)
    )
    hard_filtered_jobs = [
        job
        for job in ai_candidates
        if is_us_internship(job) and is_target_timing(job, profile)
    ]
    print(
        f"[FILTER] {len(ai_candidates)} AI Engineer candidate(s) retained "
        f"from {len(jobs)} normalized roles"
    )
    print(
        f"[FILTER] {len(hard_filtered_jobs)} target-date U.S. AI internships retained "
        f"after internship/date/location filters"
    )

    state_path = ROOT / "data" / "state.json"
    tracker_path = ROOT / "data" / "applications.json"
    tracker = ApplicationTracker(tracker_path)
    state = load_state(state_path)
    seen = set(str(value) for value in state["seen_ids"])
    tracked_statuses = tracker.status_map()
    scored: list[ScoredJob] = []
    suppressed = 0
    inactive_suppressed = 0
    duplicate_suppressed = 0
    for job in hard_filtered_jobs:
        score = score_job(job, profile)
        suppressing_record = tracker.suppression_match(
            job,
            include_previous=not show_history,
        )
        status = tracked_statuses.get(job.tracking_id, "New")
        if suppressing_record:
            status = str(suppressing_record.get("status", status))
        if not show_history and (
            status in SUPPRESSED_STATUSES or suppressing_record is not None
        ):
            suppressed += 1
            if status in SUPPRESSED_STATUSES:
                inactive_suppressed += 1
            else:
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
            f"[HISTORY] Suppressed {suppressed} tracked job(s): "
            f"{inactive_suppressed} inactive and {duplicate_suppressed} previous/duplicate. "
            "Use --show-history to include tracked history."
        )
    scored, current_duplicate_excluded = _deduplicate_scored(scored)
    print(f"[DEBUG] excluded_already_applied_or_inactive = {inactive_suppressed}")
    print(f"[DEBUG] excluded_duplicate_jobs = {current_duplicate_excluded}")
    print(f"[DEBUG] excluded_similar_previous_recommendations = {duplicate_suppressed}")
    print(f"[DEBUG] excluded_pure_swe = {pure_swe_excluded}")
    print(f"[DEBUG] excluded_not_ai_engineer = {not_ai_excluded}")
    print(f"[DEBUG] excluded_wrong_date = {wrong_date_excluded}")
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
    reason_counts = Counter(tracker.feedback_summary())
    suggestions: list[str] = []
    reason_text = " ".join(reason_counts.keys()).lower()
    if "too pure swe" in reason_text or "too swe" in reason_text:
        suggestions.append("Increase penalties for backend/frontend/mobile-only titles unless AI keywords are present.")
    if "not ai/agentic enough" in reason_text or "not ai focused" in reason_text:
        suggestions.append("Require stronger LLM, agent, RAG, automation, evaluation, or AI-product evidence before selection.")
    if "too competitive" in reason_text:
        suggestions.append("Shift more Target/Safe slots toward smaller companies, applied AI tools, and analytics-adjacent AI roles.")
    if "bad location" in reason_text:
        suggestions.append("Down-rank repeated locations the user rejects unless the role is remote U.S.")
    correction_log = CorrectionLog(
        history_excluded=suppressed,
        duplicate_history_excluded=duplicate_suppressed,
        duplicate_jobs_excluded=current_duplicate_excluded,
        pure_swe_excluded=pure_swe_excluded,
        full_time_excluded=full_time_excluded,
        non_us_excluded=non_us_excluded,
        wrong_date_excluded=wrong_date_excluded,
        not_ai_engineer_excluded=not_ai_excluded,
        inactive_history_excluded=inactive_suppressed,
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
