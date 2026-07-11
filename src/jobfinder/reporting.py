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
class BucketSelectionDiagnostics:
    requested: int
    selected: int
    eligible_before_constraints: int
    timing_rejected: int = 0
    history_suppressed: int = 0
    duplicate_suppressed: int = 0
    company_cap_blocked: int = 0
    size_mix_blocked: int = 0
    relevance_rejected: int = 0
    available_companies: int = 0
    preferred_floor: float = 0.0
    effective_floor: float = 0.0
    preferred_floor_eligible: int = 0
    adaptive_floor_eligible: int = 0
    adjacent_fallback_selected: int = 0
    company_cap_relaxed_selected: int = 0


@dataclass(frozen=True)
class BucketSelection:
    roles: tuple[ScoredJob, ...]
    fill_note: str = ""
    diagnostics: BucketSelectionDiagnostics | None = None


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
    target_total = per_bucket * len(BUCKETS)
    preferred_eligible = [item for item in ranked if _eligible(item, floor)]
    floor_steps = [
        value
        for value in (floor, 4.0, 3.5, 3.0)
        if value <= floor
    ]
    floor_steps = list(dict.fromkeys(floor_steps))
    effective_floor = floor_steps[-1] if floor_steps else floor
    eligible = preferred_eligible
    for candidate_floor in floor_steps:
        candidate = [item for item in ranked if _eligible(item, candidate_floor)]
        effective_floor = candidate_floor
        eligible = candidate
        if len(candidate) >= target_total:
            break
    selected: dict[str, list[ScoredJob]] = {bucket: [] for bucket in BUCKETS}
    used: set[str] = set()
    company_counts: Counter[str] = Counter()
    size_counts: Counter[str] = Counter()
    notes: dict[str, str] = {bucket: "" for bucket in BUCKETS}
    adjacent_fallback_counts: Counter[str] = Counter()
    relaxed_company_counts: Counter[str] = Counter()
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
    fallback_order = {
        "Reach": ("Reach", "Target", "Safe"),
        "Target": ("Target", "Safe", "Reach"),
        "Safe": ("Safe", "Target", "Reach"),
    }

    def choose(
        bucket: str,
        size: str | None = None,
        *,
        allowed_buckets: tuple[str, ...] | None = None,
        enforce_company_cap: bool = True,
    ) -> ScoredJob | None:
        allowed = allowed_buckets or (bucket,)
        candidates = [
            item
            for item in eligible
            if item.job.id not in used
            and item.score.bucket in allowed
            and (not enforce_company_cap or company_counts[item.job.company] < 2)
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

    def take_item(
        bucket: str,
        item: ScoredJob,
        *,
        relaxed_company_cap: bool = False,
    ) -> None:
        selected[bucket].append(item)
        used.add(item.job.id)
        company_counts[item.job.company] += 1
        size_counts[company_size_group(item.job)] += 1
        if item.score.bucket != bucket:
            adjacent_fallback_counts[bucket] += 1
            notes[bucket] = (
                f"{notes[bucket]} Selected {item.job.title} as an adjacent "
                f"{bucket} fallback; its natural bucket is {item.score.bucket}."
            ).strip()
        if relaxed_company_cap:
            relaxed_company_counts[bucket] += 1
            notes[bucket] = (
                f"{notes[bucket]} Relaxed the two-role company cap because no "
                "other hard-filter-valid role was available for that slot."
            ).strip()

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
                take_item(bucket, item)

    for bucket in BUCKETS:
        while len(selected[bucket]) < per_bucket:
            item = choose(bucket)
            if item is None:
                break
            take_item(bucket, item)
            notes[bucket] = (
                f"{notes[bucket]} Filled a missing size slot with the strongest "
                "remaining diverse U.S. internship."
            ).strip()
        while len(selected[bucket]) < per_bucket:
            item = choose(bucket, allowed_buckets=fallback_order[bucket])
            if item is None:
                break
            take_item(bucket, item)
        while len(selected[bucket]) < per_bucket:
            item = choose(
                bucket,
                allowed_buckets=fallback_order[bucket],
                enforce_company_cap=False,
            )
            if item is None:
                break
            take_item(bucket, item, relaxed_company_cap=True)
        if len(selected[bucket]) < per_bucket:
            notes[bucket] = (
                f"{notes[bucket]} Only {len(selected[bucket])} qualifying roles "
                f"were available; {per_bucket - len(selected[bucket])} slot(s) "
                "remain unfilled rather than violating hard filters."
            ).strip()

    return {
        bucket: BucketSelection(
            tuple(selected[bucket]),
            notes[bucket],
            BucketSelectionDiagnostics(
                requested=per_bucket,
                selected=len(selected[bucket]),
                eligible_before_constraints=sum(
                    1 for item in eligible if item.score.bucket == bucket
                ),
                company_cap_blocked=sum(
                    1
                    for item in eligible
                    if item.job.id not in used and company_counts[item.job.company] >= 2
                ),
                size_mix_blocked=notes[bucket].count("planned"),
                available_companies=len(
                    {
                        item.job.company
                        for item in eligible
                        if item.score.bucket == bucket
                    }
                ),
                preferred_floor=floor,
                effective_floor=effective_floor,
                preferred_floor_eligible=len(preferred_eligible),
                adaptive_floor_eligible=len(eligible),
                adjacent_fallback_selected=adjacent_fallback_counts[bucket],
                company_cap_relaxed_selected=relaxed_company_counts[bucket],
            ),
        )
        for bucket in BUCKETS
    }


def _shortage_explanation(
    buckets: dict[str, BucketSelection],
    correction_log: CorrectionLog | None,
) -> list[str]:
    lines: list[str] = []
    timing = correction_log.wrong_date_excluded if correction_log else 0
    history = correction_log.history_excluded if correction_log else 0
    duplicate = (
        correction_log.duplicate_jobs_excluded + correction_log.duplicate_history_excluded
        if correction_log
        else 0
    )
    relevance = correction_log.not_ai_engineer_excluded if correction_log else 0
    for bucket in BUCKETS:
        diagnostics = buckets[bucket].diagnostics
        if not diagnostics or diagnostics.selected >= diagnostics.requested:
            continue
        missing = diagnostics.requested - diagnostics.selected
        reasons: list[str] = []
        if diagnostics.eligible_before_constraints < diagnostics.requested:
            reasons.append(
                f"only {diagnostics.eligible_before_constraints} eligible {bucket} roles remained"
            )
        if diagnostics.company_cap_blocked:
            reasons.append(
                f"{diagnostics.company_cap_blocked} qualifying role(s) would violate the two-role-per-company cap"
            )
        if diagnostics.size_mix_blocked:
            reasons.append(
                f"{diagnostics.size_mix_blocked} planned company-size slot(s) could not be filled"
            )
        if timing:
            reasons.append(f"{timing} timing hard reject(s)")
        if history:
            reasons.append(f"{history} tracker/history suppression(s)")
        if duplicate:
            reasons.append(f"{duplicate} duplicate or near-duplicate suppression(s)")
        if relevance:
            reasons.append(f"{relevance} low career-relevance rejection(s)")
        if not reasons:
            reasons.append("not enough eligible U.S. internships after hard filters")
        lines.append(
            f"{bucket}: selected {diagnostics.selected} of {diagnostics.requested}; "
            f"missing {missing} because " + "; ".join(reasons) + "."
        )
    return lines


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
        f"- **Degree eligibility:** {score.degree_status}"
        + (f" - {score.degree_reason}" if score.degree_reason else ""),
        f"- **Work authorization:** {score.work_authorization_status}"
        + (
            f" - {score.work_authorization_reason}"
            if score.work_authorization_reason
            else ""
        ),
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
        *(
            [
                "",
                (
                    f"**Bucket fallback note:** Natural bucket was {score.bucket}; "
                    f"selected in {bucket} because adjacent fallback was needed."
                ),
            ]
            if score.bucket != bucket
            else []
        ),
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
        f"- **Companies with source errors:** {stats.companies_searched - stats.companies_succeeded}",
        (
            "- **Healthy complete sources with no internships:** "
            f"{source_status_counts['healthy_complete_no_internships'] + source_status_counts['healthy_no_internships']}"
        ),
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
        shortage_lines = _shortage_explanation(buckets, correction_log)
        lines.extend(
            [
                "- **Why fewer than 15 roles:** actual selector diagnostics are listed below; empty slots remain empty rather than weakening U.S.-internship, timing, relevance, company-cap, or tracker constraints.",
                "",
            ]
        )
        lines.extend(f"- {reason}" for reason in shortage_lines)
        lines.append("")
    diagnostics = [
        selection.diagnostics
        for selection in buckets.values()
        if selection.diagnostics is not None
    ]
    if diagnostics:
        effective_floor = min(item.effective_floor for item in diagnostics)
        preferred_eligible = max(item.preferred_floor_eligible for item in diagnostics)
        adaptive_eligible = max(item.adaptive_floor_eligible for item in diagnostics)
        fallback_recovered = sum(item.adjacent_fallback_selected for item in diagnostics)
        company_cap_relaxed = sum(
            item.company_cap_relaxed_selected for item in diagnostics
        )
        lines.extend(
            [
                f"- **Preferred score-floor eligible:** {preferred_eligible}",
                f"- **Adaptive score-floor eligible:** {adaptive_eligible}",
                f"- **Final effective score floor:** {effective_floor:.1f}",
                f"- **Adjacent fallback recovered:** {fallback_recovered}",
                f"- **Company-cap relaxations:** {company_cap_relaxed}",
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
        healthy_statuses = {
            "healthy_complete",
            "healthy_complete_no_internships",
            "healthy_with_internships",
            "healthy_no_internships",
        }
        healthy = (
            source_status_counts["healthy_complete"]
            + source_status_counts["healthy_with_internships"]
        )
        healthy_empty = (
            source_status_counts["healthy_complete_no_internships"]
            + source_status_counts["healthy_no_internships"]
        )
        temporary = (
            source_status_counts["partial_results"]
            + source_status_counts["rate_limited"]
        )
        config_errors = (
            source_status_counts["invalid_configuration"]
            + source_status_counts["invalid_board_identifier"]
            + source_status_counts["invalid_response"]
        )
        ats_changed = source_status_counts["official_source_changed"] + source_status_counts["ats_changed"]
        unsupported = (
            source_status_counts["unsupported_provider"]
            + source_status_counts["official_page_unstructured"]
            + source_status_counts["unsupported_source"]
            + source_status_counts["disabled_intentionally"]
        )
        parser_failures = (
            source_status_counts["parser_suspected_broken"]
            + source_status_counts["parser_failure"]
        )
        unhealthy = [
            item
            for item in stats.source_health
            if str(getattr(item, "status", "")) not in healthy_statuses
        ]
        no_internships = [
            item for item in stats.source_health
            if str(getattr(item, "status", "")) in {
                "healthy_complete_no_internships",
                "healthy_no_internships",
            }
        ]
        lines.extend(
            [
                "## Source Health",
                "",
                f"- **Total configured:** {len(stats.source_health) or stats.companies_searched}",
                f"- **Healthy complete with internships:** {healthy}",
                f"- **Healthy complete but no internships:** {healthy_empty}",
                f"- **Partial/rate-limited results:** {temporary}",
                f"- **Blocked:** {source_status_counts['blocked'] + source_status_counts['blocked_or_forbidden']}",
                f"- **Invalid configuration:** {config_errors}",
                f"- **Official source changed:** {ats_changed}",
                f"- **Unsupported/unstructured pages:** {unsupported}",
                f"- **Parser suspected broken:** {parser_failures}",
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
        if unhealthy:
            lines.extend(["**Unhealthy sources:**", ""])
            for item in unhealthy:
                detail_parts = [
                    str(getattr(item, "endpoint", "")).strip(),
                    str(getattr(item, "error_code", "")).strip(),
                    str(getattr(item, "message", "")).strip(),
                ]
                detail = " ".join(part for part in detail_parts if part)
                suggested = str(getattr(item, "suggested_fix", "")).strip()
                lines.append(
                    f"- {getattr(item, 'company', 'Unknown')} "
                    f"({getattr(item, 'provider', 'unknown')}): "
                    f"{getattr(item, 'status', 'unknown')}"
                    + (f" - {detail}" if detail else "")
                    + (f". Suggested fix: {suggested}" if suggested else ".")
                )
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
