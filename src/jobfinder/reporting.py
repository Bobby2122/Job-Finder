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


@dataclass(frozen=True)
class BucketSelection:
    roles: tuple[ScoredJob, ...]
    fill_note: str = ""


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
    ranked = sorted(scored, key=lambda item: item.score.overall, reverse=True)
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
            adjusted = item.score.overall - repetition_penalty - affinity_penalty
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
        f"Relevance {score.skill_fit:.1f} | Internship clarity "
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
    if item.is_new:
        badges.append("NEW")
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
        f"- **Location:** {job.location}",
        f"- **Employment type:** {job.employment_type or 'Not listed'}",
        f"- **Start timing:** {job.start_year_or_season}",
        f"- **Role family:** {job.role_family or 'Not classified'}",
        f"- **Source:** {job.source}",
        "",
        _score_line(item),
        "",
        f"**Competitiveness:** {score.competitiveness}",
        "",
        "**Why it matches Bobby:** " + "; ".join(score.why_match) + ".",
        "",
        f"**Why it is {bucket}:** {_bucket_reason(item, bucket)}",
        "",
        f"**Main gap to prepare:** {score.concerns[0]}.",
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
    buckets = select_buckets(scored, floor, per_bucket)
    selected = [
        item for bucket in BUCKETS for item in buckets[bucket].roles
    ]
    selected_ids = {item.job.id for item in selected}
    size_distribution = Counter(company_size_group(item.job) for item in selected)
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
    if stats.source_failures:
        lines.extend(
            [
                "## Source Health",
                "",
                (
                    f"{len(stats.source_failures)} source(s) failed without stopping "
                    "the report. Review the GitHub Actions log for company-level details."
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Method",
            "",
            "Ease-adjusted score = relevance 30% + internship clarity 20% + "
            "competition ease 20% + requirement ease 15% + U.S. stability 10% "
            "+ practical value 5%, minus popularity penalties. Final selection "
            "then applies a two-role company cap and large/mid/startup mix targets.",
            "",
        ]
    )
    return "\n".join(lines)
