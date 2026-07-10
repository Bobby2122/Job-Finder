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
    is_internship_role,
    is_us_internship,
    is_us_location,
    score_job,
)
from .sources import MultiCompanyClient, load_sources_config
from .state import load_state, recommendation_state, save_state, update_state
from .tracker import ApplicationTracker, SUPPRESSED_STATUSES


ROOT = Path(__file__).resolve().parents[2]
MAJOR_EMPLOYERS = (
    "Google",
    "Apple",
    "Microsoft",
    "Amazon",
    "Meta",
    "NVIDIA",
    "IBM",
    "Adobe",
    "Salesforce",
    "Oracle",
    "Uber",
    "Airbnb",
    "LinkedIn",
    "Tesla",
    "Autodesk",
    "MathWorks",
)


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
    selected, excluded_items = _deduplicate_scored_with_exclusions(scored)
    return selected, len(excluded_items)


def _deduplicate_scored_with_exclusions(
    scored: list[ScoredJob],
) -> tuple[list[ScoredJob], list[ScoredJob]]:
    selected: list[ScoredJob] = []
    excluded: list[ScoredJob] = []
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
        excluded.append(item)
        existing = selected[duplicate_index]
        if item.score.overall > existing.score.overall:
            excluded[-1] = existing
            selected[duplicate_index] = item
    return selected, excluded


def _diagnostic(
    item: ScoredJob,
    *,
    stage: str,
    primary_reason: str,
    secondary_reasons: list[str] | None = None,
) -> dict[str, object]:
    score = item.score
    return {
        "company": item.job.company,
        "title": item.job.title,
        "url": item.job.url,
        "stage": stage,
        "primary_reason": primary_reason,
        "secondary_reasons": secondary_reasons or [],
        "timing_tier": score.timing_tier,
        "role_classification": score.role_classification,
        "relevance_score": score.relevance_total,
        "overall_score": score.overall,
    }


def _write_source_health(path: Path, source_health: tuple[object, ...]) -> None:
    records = [
        item.as_dict() if hasattr(item, "as_dict") else dict(item)  # type: ignore[arg-type]
        for item in source_health
    ]
    path.write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_source_coverage(
    path: Path,
    sources_config: dict | None,
    source_health: tuple[object, ...],
) -> None:
    configured = {
        str(source.get("company", "")): source
        for source in (sources_config or {}).get("sources", [])
    }
    health_by_company = {
        str(getattr(item, "company", "")): item for item in source_health
    }
    rows = [
        "Company | Configured | Enabled | Adapter | Health | Internships found | Notes",
        "--- | --- | --- | --- | --- | ---: | ---",
    ]
    for company in MAJOR_EMPLOYERS:
        source = configured.get(company)
        health = health_by_company.get(company)
        if source:
            status = str(getattr(health, "status", "not_checked"))
            internships = int(getattr(health, "internship_roles", 0)) if health else 0
            note = (
                str(getattr(health, "recommended_action", ""))
                if health
                else "Configured but not checked in this run."
            )
            rows.append(
                " | ".join(
                    (
                        company,
                        "yes",
                        "yes" if source.get("enabled", True) else "no",
                        str(source.get("adapter", "")),
                        status,
                        str(internships),
                        note or str(source.get("latest_status", "")),
                    )
                )
            )
        else:
            rows.append(
                " | ".join(
                    (
                        company,
                        "no",
                        "no",
                        "",
                        "not_configured",
                        "0",
                        "No maintainable official adapter was added in this task; investigate and add only when an official source can be supported without guessing identifiers.",
                    )
                )
            )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


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
        sources_config = None
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
            result = MultiCompanyClient(max_workers=12).search(sources_config)
            jobs = result.roles
            stats = ReportStats(
                companies_searched=result.companies_attempted,
                companies_succeeded=result.companies_succeeded,
                raw_roles_found=result.raw_roles_found,
                source_failures=result.failures,
                source_health=result.source_health,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    low_relevance_excluded = 0
    pure_swe_excluded = 0
    wrong_date_excluded = 0
    diagnostics: list[dict[str, object]] = []
    non_us_excluded = sum(1 for job in jobs if not is_us_location(job))
    full_time_excluded = sum(
        1 for job in jobs if is_us_location(job) and not is_internship_role(job)
    )
    hard_filtered_jobs = [
        job
        for job in jobs
        if is_us_internship(job)
    ]
    print(
        f"[FILTER] {len(hard_filtered_jobs)} U.S. internships retained after "
        "location/employment hard filters"
    )

    state_path = ROOT / "data" / "state.json"
    tracker_path = ROOT / "data" / "applications.json"
    tracker = ApplicationTracker(tracker_path)
    state = load_state(state_path)
    tracked_statuses = tracker.status_map()
    scored: list[ScoredJob] = []
    suppressed = 0
    inactive_suppressed = 0
    duplicate_suppressed = 0
    internship_like_roles = 0
    timing_counts: Counter[str] = Counter()
    seen_hard_filter_ids: set[str] = set()
    for job in jobs:
        if is_internship_role(job):
            internship_like_roles += 1
        if job in hard_filtered_jobs:
            continue
        score = score_job(job, profile)
        stage = "non_us_hard_filter" if not is_us_location(job) else "non_internship_hard_filter"
        diagnostics.append(
            _diagnostic(
                ScoredJob(job, score),
                stage=stage,
                primary_reason=score.rejection_reason
                or (
                    "Outside the U.S."
                    if stage == "non_us_hard_filter"
                    else "Not an explicit internship or is full-time/new-grad"
                ),
            )
        )
    for job in hard_filtered_jobs:
        seen_hard_filter_ids.add(job.tracking_id)
        score = score_job(job, profile)
        timing_counts[score.timing_tier] += 1
        if score.rejection_reason:
            if score.timing_tier == "Hard reject":
                wrong_date_excluded += 1
                stage = "timing_hard_rejected"
            elif score.role_classification == "pure_swe":
                pure_swe_excluded += 1
                stage = "pure_swe_hard_rejected"
            else:
                low_relevance_excluded += 1
                stage = "low_relevance_hard_rejected"
            diagnostics.append(
                _diagnostic(
                    ScoredJob(job, score),
                    stage=stage,
                    primary_reason=score.rejection_reason,
                    secondary_reasons=list(score.concerns),
                )
            )
            continue
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
            diagnostics.append(
                _diagnostic(
                    ScoredJob(job, score, tracking_status=status),
                    stage="history_suppressed",
                    primary_reason=f"Suppressed by tracker/history status: {status}",
                )
            )
            continue
        display_status = status
        if status == "New":
            display_status = recommendation_state(state, job.tracking_id, status)
        scored.append(
            ScoredJob(
                job,
                score,
                is_new=display_status in {"New", "NEWLY QUALIFIED"},
                tracking_status=display_status,
            )
        )
    if suppressed:
        print(
            f"[HISTORY] Suppressed {suppressed} tracked job(s): "
            f"{inactive_suppressed} inactive and {duplicate_suppressed} previous/duplicate. "
            "Use --show-history to include tracked history."
        )
    scored, duplicate_items = _deduplicate_scored_with_exclusions(scored)
    current_duplicate_excluded = len(duplicate_items)
    for item in duplicate_items:
        diagnostics.append(
            _diagnostic(
                item,
                stage="current_duplicates",
                primary_reason="Duplicate or near-duplicate role in current run",
            )
        )
    print(f"[DEBUG] excluded_already_applied_or_inactive = {inactive_suppressed}")
    print(f"[DEBUG] excluded_duplicate_jobs = {current_duplicate_excluded}")
    print(f"[DEBUG] excluded_similar_previous_recommendations = {duplicate_suppressed}")
    print(f"[DEBUG] excluded_pure_swe = {pure_swe_excluded}")
    print(f"[DEBUG] excluded_low_career_relevance = {low_relevance_excluded}")
    print(f"[DEBUG] excluded_wrong_date = {wrong_date_excluded}")
    print(f"[RUN STATS] companies_scanned = {stats.companies_searched}")
    print(f"[RUN STATS] successful_crawlers = {stats.companies_succeeded}")
    failed_crawlers = stats.companies_searched - stats.companies_succeeded
    internship_postings_found = sum(
        int(getattr(item, "internship_roles", 0)) for item in stats.source_health
    )
    print(f"[RUN STATS] failed_crawlers = {failed_crawlers}")
    print(f"[RUN STATS] internship_postings_found = {internship_postings_found}")
    print(f"[RUN STATS] surviving_hard_filters = {len(hard_filtered_jobs)}")
    print(f"[RUN STATS] selected_for_ranking = {len(scored)}")
    print(f"[FUNNEL] raw_roles = {len(jobs)}")
    print(f"[FUNNEL] internship_like_roles = {internship_like_roles}")
    print(f"[FUNNEL] us_internships = {len(hard_filtered_jobs)}")
    print(f"[FUNNEL] timing_hard_rejected = {wrong_date_excluded}")
    print(f"[FUNNEL] pure_swe_hard_rejected = {pure_swe_excluded}")
    print(f"[FUNNEL] low_relevance_hard_rejected = {low_relevance_excluded}")
    print(f"[FUNNEL] history_suppressed = {suppressed}")
    print(f"[FUNNEL] current_duplicates = {current_duplicate_excluded}")
    print(f"[FUNNEL] eligible_for_ranking = {len(scored)}")
    print(f"[FUNNEL] timing_tier_a_count = {timing_counts['A']}")
    print(f"[FUNNEL] timing_tier_b_count = {timing_counts['B']}")
    print(f"[FUNNEL] timing_tier_c_count = {timing_counts['C']}")
    print(f"[FUNNEL] timing_unknown_count = {timing_counts['Unknown']}")
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
        not_ai_engineer_excluded=low_relevance_excluded,
        inactive_history_excluded=inactive_suppressed,
        rejection_reasons=tuple(
            f"{reason} ({count})" for reason, count in reason_counts.most_common(8)
        ),
        suggestions=tuple(suggestions),
    )
    buckets = select_buckets(scored, top_floor, per_bucket=per_bucket)
    tracked_recommendations = [
        (item, bucket)
        for bucket in ("Reach", "Target", "Safe")
        for item in buckets[bucket].roles
    ]
    alert_candidates = [item for item, _bucket in tracked_recommendations]
    new_recommendations = sum(1 for item in alert_candidates if item.tracking_status == "New")
    newly_qualified_recommendations = sum(
        1 for item in alert_candidates if item.tracking_status == "NEWLY QUALIFIED"
    )
    print(f"[FUNNEL] selected_for_report = {len(alert_candidates)}")
    print(f"[FUNNEL] new_recommendations = {new_recommendations}")
    print(
        "[FUNNEL] newly_qualified_recommendations = "
        f"{newly_qualified_recommendations}"
    )
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
    diagnostics_path = report_dir / "filter_diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if stats.source_health:
        _write_source_health(report_dir / "source_health.json", stats.source_health)
        _write_source_coverage(
            report_dir / "source_coverage.md",
            sources_config,
            stats.source_health,
        )

    tracker.upsert_recommendations(tracked_recommendations)
    state = update_state(
        state,
        discovered_ids=(job.tracking_id for job in jobs),
        recommended_ids=(item.job.tracking_id for item, _bucket in tracked_recommendations),
    )
    save_state(state_path, state)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)

    urgent_threshold = float(thresholds["urgent_apply"])
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
