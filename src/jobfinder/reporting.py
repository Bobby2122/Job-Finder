from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import ScoredJob
from .scoring import company_size_group, is_us_internship


BUCKETS = ("Reach", "Target", "Safe")


@dataclass(frozen=True)
class ReportStats:
    companies_searched: int = 0
    companies_succeeded: int = 0
    raw_roles_found: int = 0
    source_failures: tuple[str, ...] = ()
    source_health: tuple[object, ...] = ()


@dataclass(frozen=True)
class BucketSelection:
    roles: tuple[ScoredJob, ...]
    fill_note: str = ""


@dataclass(frozen=True)
class CorrectionLog:
    history_excluded: int = 0
    inactive_history_excluded: int = 0
    duplicate_jobs_excluded: int = 0
    duplicate_history_excluded: int = 0
    pure_swe_excluded: int = 0
    not_ai_engineer_excluded: int = 0
    full_time_excluded: int = 0
    non_us_excluded: int = 0
    wrong_date_excluded: int = 0
    rejection_reasons: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


def _eligible(item: ScoredJob, floor: float) -> bool:
    title = item.job.title.lower()
    senior_only = any(
        term in title
        for term in (
            "senior",
            "sr.",
            "sr ",
            "staff",
            "principal",
            "director",
            "head of",
            "manager",
        )
    )
    return bool(
        is_us_internship(item.job)
        and item.score.relevant
        and item.score.geography_ok
        and not item.score.rejection_reason
        and not senior_only
        and item.score.overall >= floor
    )


def _take_diverse(items: list[ScoredJob], limit: int) -> list[ScoredJob]:
    selected: list[ScoredJob] = []
    counts: Counter[str] = Counter()
    for item in items:
        if counts[item.job.company] < 2:
            selected.append(item)
            counts[item.job.company] += 1
        if len(selected) == limit:
            return selected
    for item in items:
        if item not in selected:
            selected.append(item)
        if len(selected) == limit:
            break
    return selected


def select_buckets(
    scored: list[ScoredJob],
    floor: float,
    per_bucket: int = 5,
) -> dict[str, BucketSelection]:
    status_penalties = {
        "Viewed": 0.8,
        "Started": 0.25,
        "Applied": 1.5,
        "Rejected": 1.5,
        "Not Interested": 1.5,
    }
    ranked = sorted(
        scored,
        key=lambda item: (
            item.score.overall
            - status_penalties.get(item.tracking_status, 0.0)
        ),
        reverse=True,
    )
    eligible = [item for item in ranked if _eligible(item, floor)]
    selected: dict[str, list[ScoredJob]] = {bucket: [] for bucket in BUCKETS}
    used: set[str] = set()
    company_counts: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    notes: dict[str, str] = {bucket: "" for bucket in BUCKETS}
    size_plan = {
        "Reach": {"Large": 2, "Mid": 2, "Small": 1},
        "Target": {"Large": 2, "Mid": 2, "Small": 1},
        "Safe": {"Large": 1, "Mid": 1, "Small": 3},
    }

    bucket_distance = {
        "Reach": {"Reach": 0, "Target": 1, "Safe": 2},
        "Target": {"Target": 0, "Safe": 1, "Reach": 1},
        "Safe": {"Safe": 0, "Target": 1, "Reach": 2},
    }

    def choose(bucket: str, size: str | None = None) -> ScoredJob | None:
        candidates = [
            item
            for item in eligible
            if item.job.id not in used
            and company_counts[item.job.company] < 2
            and (size is None or company_size_group(item.job) == size)
        ]
        if not candidates:
            return None

        def rank(item: ScoredJob) -> tuple[float, float, float]:
            repetition_penalty = 1.25 * company_counts[item.job.company]
            affinity_penalty = 0.65 * bucket_distance[bucket][item.score.bucket]
            history_penalty = status_penalties.get(item.tracking_status, 0.0)
            adjusted = (
                item.score.overall
                - repetition_penalty
                - affinity_penalty
                - history_penalty
            )
            return (
                adjusted,
                item.score.competition_ease,
                item.score.requirement_ease,
            )

        return max(candidates, key=rank)

    for bucket in BUCKETS:
        for size, count in size_plan[bucket].items():
            for _ in range(count):
                item = choose(bucket, size)
                if item is None:
                    notes[bucket] = (
                        f"{notes[bucket]} Could not fill the planned {size} slot "
                        "without violating U.S.-internship or company-cap rules."
                    ).strip()
                    continue
                selected[bucket].append(item)
                used.add(item.job.id)
                company_counts[item.job.company] += 1
                size_counts[size] += 1

    for bucket in BUCKETS:
        while len(selected[bucket]) < per_bucket:
            item = choose(bucket)
            if item is None:
                break
            selected[bucket].append(item)
            used.add(item.job.id)
            company_counts[item.job.company] += 1
            size_counts[company_size_group(item.job)] += 1
            notes[bucket] = (
                f"{notes[bucket]} Filled a missing size slot with the strongest "
                "remaining diverse U.S. internship."
            ).strip()
        if len(selected[bucket]) < per_bucket:
            notes[bucket] = (
                f"{notes[bucket]} Only {len(selected[bucket])} qualifying roles "
                f"were available; {per_bucket - len(selected[bucket])} slot(s) "
                "remain unfilled rather than violating hard filters."
            ).strip()

    return {
        bucket: BucketSelection(tuple(selected[bucket]), notes[bucket])
        for bucket in BUCKETS
    }


def _score_line(item: ScoredJob) -> str:
    score = item.score
    return (
        f"**Ease-adjusted score {score.overall:.2f}/10** - "
        f"Fit {score.skill_fit:.1f} | Internship clarity "
        f"{score.internship_clarity:.1f} | Competition ease "
        f"{score.competition_ease:.1f} | Requirement ease "
        f"{score.requirement_ease:.1f} | U.S. stability {score.us_stability:.1f}"
    )


def _bucket_reason(item: ScoredJob, bucket: str) -> str:
    if bucket == "Reach":
        return (
            "The role is relevant but carries higher popularity, research depth, "
            "or technical competition."
        )
    if bucket == "Safe":
        return (
            "The internship has a clearer, lower-complexity path to interview while "
            "retaining useful analytics, risk, operations, or data experience."
        )
    return (
        "The internship balances strong relevance with a realistic undergraduate "
        "hiring bar."
    )


def _job_block(item: ScoredJob, bucket: str, urgent_threshold: float) -> list[str]:
    job, score = item.job, item.score
    badges: list[str] = []
    if item.tracking_status == "New":
        badges.append("NEW")
    elif item.tracking_status == "NEWLY QUALIFIED":
        badges.append("NEWLY QUALIFIED")
    elif item.tracking_status == "PREVIOUSLY RECOMMENDED":
        badges.append("PREVIOUSLY RECOMMENDED")
    elif item.tracking_status != "New":
        badges.append(item.tracking_status.upper())
    if score.overall >= urgent_threshold:
        badges.append("URGENT APPLY")
    suffix = f" - {' / '.join(badges)}" if badges else ""
    return [
        f"### [{job.title}]({job.url}){suffix}",
        "",
        f"- **Company:** {job.company}",
        f"- **Company size/category:** {job.company_size_category}",
        f"- **Size group:** {company_size_group(job)}",
        f"- **Source category:** {job.source_category}",
        f"- **Application status:** {item.tracking_status}",
        f"- **Tracker ID:** `{job.tracking_id}`",
        f"- **Location:** {job.location}",
        f"- **Employment type:** {job.employment_type or 'Not listed'}",
        f"- **Application link:** {job.url}",
        f"- **Source link:** {job.url}",
        f"- **Start timing:** {job.start_year_or_season}",
        f"- **Timing:** Tier {score.timing_tier}",
        f"- **Timing reason:** {score.timing_reason or 'No timing concern detected'}",
        f"- **Timing confidence:** {score.timing_confidence}",
        f"- **Role family:** {job.role_family or 'Not classified'}",
        f"- **Role classification:** {score.role_classification}",
        f"- **Role classification reason:** {score.role_classification_reason}",
        f"- **Role classification confidence:** {score.role_classification_confidence}",
        f"- **Source:** {job.source}",
        "",
        _score_line(item),
        "",
        f"**Competitiveness:** {score.competitiveness}",
        "",
        "**Why it matches Bobby:** " + "; ".join(score.why_match) + ".",
        "",
        f"**AI/agentic relevance:** {score.ai_focus}.",
        "",
        (
            "**Career relevance breakdown:** "
            f"AI {score.ai_relevance_score:.1f}/35 | "
            f"OR/Optimization {score.optimization_relevance_score:.1f}/25 | "
            f"Applied Math {score.applied_math_relevance_score:.1f}/20 | "
            f"Data/Stats {score.data_relevance_score:.1f}/15 | "
            f"Quant/Risk {score.quant_relevance_score:.1f}/5 | "
            f"Total {score.relevance_total:.1f}"
        ),
        "",
        f"**Primary track:** {score.primary_track or 'Not classified'}.",
        "",
        f"**AI Engineer classifier:** {'Passed' if score.ai_engineer else 'Failed'} - {score.ai_classification_reason}.",
        "",
        "**Matched AI keywords:** "
        + (", ".join(score.ai_keywords) if score.ai_keywords else "None explicit; verify scope."),
        "",
        f"**Pure SWE vs AI-focused:** {'Potential pure SWE concern' if score.pure_swe_signal else score.ai_focus}.",
        "",
        f"**Why it is {bucket}:** {_bucket_reason(item, bucket)}",
        "",
        "**Potential concerns:** " + "; ".join(score.concerns) + ".",
        "",
    ]


def _market_signals(items: Iterable[ScoredJob]) -> list[str]:
    text = " ".join(item.job.text for item in items)
    signals: list[str] = []
    if "pytorch" in text or "tensorflow" in text:
        signals.append(
            "Add one visible PyTorch project; current roles repeatedly ask for a "
            "mainstream deep-learning framework."
        )
    if "sql" in text:
        signals.append(
            "Keep SQL interview-ready, especially joins, windows, experiment "
            "analysis, and metric design."
        )
    if "statistics" in text or "experiment" in text:
        signals.append(
            "Practice experiment design, statistical inference, and communicating "
            "uncertainty in business terms."
        )
    if "data pipeline" in text or "cloud" in text or "distributed" in text:
        signals.append(
            "Build one production-flavored project with a data pipeline, cloud "
            "deployment, evaluation layer, and documented tradeoffs."
        )
    return signals[:4] or [
        "Strengthen applied statistics and Python project evidence while preserving "
        "the unusual math/ML research positioning."
    ]


def build_report(
    scored: list[ScoredJob],
    profile: dict,
    generated_at: datetime,
    stats: ReportStats | None = None,
    bucket_selections: dict[str, BucketSelection] | None = None,
    correction_log: CorrectionLog | None = None,
) -> str:
    stats = stats or ReportStats(
        companies_searched=len({item.job.company for item in scored}),
        companies_succeeded=len({item.job.company for item in scored}),
        raw_roles_found=len(scored),
    )
    thresholds = profile["thresholds"]
    urgent = float(thresholds["urgent_apply"])
    floor = float(thresholds["top_opportunity"])
    rejected_floor = float(thresholds["interesting_reject"])
    per_bucket = int(thresholds.get("max_per_bucket", 5))
    buckets = bucket_selections or select_buckets(scored, floor, per_bucket)
    selected = [
        item for bucket in BUCKETS for item in buckets[bucket].roles
    ]
    selected_ids = {item.job.id for item in selected}
    size_distribution = Counter(company_size_group(item.job) for item in selected)
    source_status_counts = Counter(
        str(getattr(item, "status", "")) for item in stats.source_health
    )
    insufficiency_reasons: list[str] = []
    internship_postings_found = sum(
        int(getattr(item, "internship_roles", 0)) for item in stats.source_health
    )
    ranked = sorted(scored, key=lambda item: item.score.overall, reverse=True)
    rejected = [
        item
        for item in ranked
        if item.job.id not in selected_ids
        and item.score.overall >= rejected_floor
        and (item.score.relevant or bool(item.score.rejection_reason))
    ][: int(thresholds["max_rejected"])]

    lines = [
        "# Multi-Company Opportunity Intelligence Report",
        "",
        (
            "Generated "
            f"{generated_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M %Z')}"
        ),
        "",
        f"- **Roles reviewed:** {stats.raw_roles_found}",
        f"- **Companies searched:** {stats.companies_searched}",
        f"- **Companies successfully read:** {stats.companies_succeeded}",
        f"- **Companies failed/unavailable:** {stats.companies_searched - stats.companies_succeeded}",
        f"- **Companies with no open internships found:** {source_status_counts['empty_but_healthy'] + source_status_counts['no_open_internships']}",
        f"- **Internship/co-op postings found:** {internship_postings_found}",
        f"- **Unique relevant roles found:** {len(scored)}",
        f"- **Roles selected:** {len(selected)}",
        (
            "- **Selected company-size mix:** "
            f"Large {size_distribution['Large']} | "
            f"Mid {size_distribution['Mid']} | "
            f"Small/startup {size_distribution['Small']}"
        ),
        "",
    ]
    if len(selected) < 15:
        failed_sources = stats.companies_searched - stats.companies_succeeded
        if failed_sources:
            insufficiency_reasons.append("data source failures")
        if source_status_counts["empty_but_healthy"] or source_status_counts["no_open_internships"]:
            insufficiency_reasons.append("current market has not opened enough internships")
        if correction_log and correction_log.wrong_date_excluded:
            insufficiency_reasons.append("timing window hard rejects")
        if correction_log and correction_log.not_ai_engineer_excluded:
            insufficiency_reasons.append("career relevance")
        if correction_log and correction_log.history_excluded:
            insufficiency_reasons.append("history deduplication")
        lines.extend(
            [
                "- **Why fewer than 15 roles:** "
                + (
                    ", ".join(dict.fromkeys(insufficiency_reasons))
                    if insufficiency_reasons
                    else "not enough eligible U.S. internships after relevance and tracker constraints"
                ),
                "",
            ]
        )

    section_names = {
        "Reach": "A",
        "Target": "B",
        "Safe": "C",
    }
    for bucket in BUCKETS:
        selection = buckets[bucket]
        lines.extend(
            [
                f"## {section_names[bucket]}. {bucket} Roles ({len(selection.roles)})",
                "",
            ]
        )
        if selection.fill_note:
            lines.extend([f"> {selection.fill_note}", ""])
        for item in selection.roles:
            lines.extend(_job_block(item, bucket, urgent))
        if not selection.roles:
            lines.extend(["No qualifying roles were available for this bucket.", ""])

    lines.extend(["## D. Rejected But Interesting", ""])
    if rejected:
        for item in rejected:
            reason = item.score.rejection_reason or "; ".join(item.score.concerns)
            lines.extend(
                [
                    f"### [{item.job.title}]({item.job.url})",
                    "",
                    f"{item.job.company} - {item.job.location} - {_score_line(item)}",
                    "",
                    f"**Rejection reason:** {reason}.",
                    "",
                ]
            )
    else:
        lines.extend(["No near-misses worth monitoring today.", ""])

    lines.extend(["## E. Strategy Advice", ""])
    for signal in _market_signals(selected):
        lines.append(f"- {signal}")
    lines.extend(
        [
            "- **Do not only apply to Reach roles. Prioritize 5–8 Target/Safe "
            "applications per Reach application.**",
            "",
        ]
    )
    if stats.source_health or stats.source_failures:
        crawler_failed = [
            item for item in stats.source_health
            if str(getattr(item, "status", "")) in {"parser_failure", "temporary_network_failure"}
        ]
        unavailable = [
            item for item in stats.source_health
            if str(getattr(item, "status", "")) in {"invalid_endpoint", "blocked"}
        ]
        no_internships = [
            item for item in stats.source_health
            if str(getattr(item, "status", "")) in {"empty_but_healthy", "no_open_internships"}
        ]
        lines.extend(
            [
                "## Source Health",
                "",
                f"- **Working:** {source_status_counts['working'] + source_status_counts['success']}",
                f"- **Empty but healthy:** {source_status_counts['empty_but_healthy'] + source_status_counts['no_open_internships']}",
                f"- **Invalid endpoint:** {source_status_counts['invalid_endpoint']}",
                f"- **Blocked:** {source_status_counts['blocked']}",
                f"- **Temporary network failure:** {source_status_counts['temporary_network_failure']}",
                f"- **Parser failure:** {source_status_counts['parser_failure']}",
                "",
            ]
        )
        if no_internships:
            lines.extend(
                [
                    "**No open internships found:** "
                    + ", ".join(
                        str(getattr(item, "company", "Unknown"))
                        for item in no_internships[:20]
                    )
                    + ("." if len(no_internships) <= 20 else ", ..."),
                    "",
                ]
            )
        if crawler_failed:
            lines.extend(["**Crawler failed:**", ""])
            for item in crawler_failed[:12]:
                lines.append(
                    f"- {getattr(item, 'company', 'Unknown')} "
                    f"({getattr(item, 'provider', 'unknown')}): "
                    f"{getattr(item, 'endpoint', '')} "
                    f"{getattr(item, 'error_code', '')} - "
                    f"{getattr(item, 'message', '')}. "
                    f"Suggested fix: {getattr(item, 'suggested_fix', '')}"
                )
            if len(crawler_failed) > 12:
                lines.append(f"- ... {len(crawler_failed) - 12} more")
            lines.append("")
        if unavailable:
            lines.extend(["**Company source unavailable / adapter mismatch:**", ""])
            for item in unavailable[:12]:
                lines.append(
                    f"- {getattr(item, 'company', 'Unknown')} "
                    f"({getattr(item, 'provider', 'unknown')}): "
                    f"{getattr(item, 'endpoint', '')} "
                    f"{getattr(item, 'error_code', '')} - "
                    f"{getattr(item, 'message', '')}. "
                    f"Suggested fix: {getattr(item, 'suggested_fix', '')}"
                )
            if len(unavailable) > 12:
                lines.append(f"- ... {len(unavailable) - 12} more")
            lines.append("")
    if correction_log:
        lines.extend(
            [
                "## Daily Filtering Report",
                "",
                "Excluded:",
                "",
                f"- {correction_log.inactive_history_excluded} already applied/rejected/not interested",
                f"- {correction_log.duplicate_jobs_excluded} duplicate jobs in this run",
                f"- {correction_log.duplicate_history_excluded} similar previous recommendations",
                f"- {correction_log.pure_swe_excluded} pure SWE without AI/ML/modeling scope",
                f"- {correction_log.not_ai_engineer_excluded} low career relevance / wrong direction",
                f"- {correction_log.wrong_date_excluded} wrong internship date",
                f"- {correction_log.full_time_excluded} wrong job type / non-internship",
                f"- {correction_log.non_us_excluded} outside the U.S.",
                "",
                "**User rejection reasons collected:**",
                "",
            ]
        )
        if correction_log.rejection_reasons:
            lines.extend(f"- {reason}" for reason in correction_log.rejection_reasons)
        else:
            lines.append("- No manual rejection reasons recorded yet.")
        lines.extend(["", "**Suggested ranking/filter improvements:**", ""])
        if correction_log.suggestions:
            lines.extend(f"- {suggestion}" for suggestion in correction_log.suggestions)
        else:
            lines.append(
                "- Keep prioritizing realistic AI/applied AI, OR/optimization, "
                "applied math, scientific computing, modeling-heavy data science, "
                "and quant/risk internships."
            )
        prompt = (
            "Update JobFinder ranking using these rejection patterns: "
            + (
                "; ".join(correction_log.rejection_reasons)
                if correction_log.rejection_reasons
                else "no new rejection reasons yet"
            )
            + ". Preserve hard filters for U.S. internships and suppress Applied, "
            "Rejected, and Not Interested roles. Keep the target portfolio broad: "
            "AI/applied AI, applied scientist/research engineer, OR/optimization, "
            "applied math/computational math, modeling-heavy data science, and "
            "quant/risk."
        )
        lines.extend(
            [
                "",
                "**Prompt for Codex improvement:**",
                "",
                f"> {prompt}",
                "",
            ]
        )
    lines.extend(
        [
            "## Method",
            "",
            "Career relevance = AI/applied AI 35% + OR/optimization 25% + "
            "applied math/computational math 20% + data/statistics 15% + "
            "quant/risk 5%, plus bonuses for math/statistics/computational "
            "science eligibility, research/modeling/algorithms/simulation, "
            "smaller-company ownership, and career-path fit. The relevance "
            "score is blended with internship clarity, accessibility, and "
            "company realism. Final selection then applies a two-role company "
            "cap, history suppression, and large/mid/startup mix targets.",
            "",
        ]
    )
    return "\n".join(lines)
