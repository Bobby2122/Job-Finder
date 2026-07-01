from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import ScoredJob


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
    senior = any(
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
        item.score.relevant
        and item.score.geography_ok
        and item.score.location_fit >= 7.0
        and not item.score.rejection_reason
        and not senior
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
    natural: dict[str, list[ScoredJob]] = {
        bucket: [item for item in eligible if item.score.bucket == bucket]
        for bucket in BUCKETS
    }
    selected: dict[str, list[ScoredJob]] = {
        bucket: _take_diverse(natural[bucket], per_bucket) for bucket in BUCKETS
    }
    used = {item.job.id for roles in selected.values() for item in roles}
    remaining = [item for item in eligible if item.job.id not in used]
    notes: dict[str, str] = {bucket: "" for bucket in BUCKETS}
    preferences = {
        "Reach": ("Target", "Safe"),
        "Target": ("Safe", "Reach"),
        "Safe": ("Target", "Reach"),
    }

    for bucket in BUCKETS:
        needed = per_bucket - len(selected[bucket])
        if not needed:
            continue
        fillers: list[ScoredJob] = []
        for source_bucket in preferences[bucket]:
            for item in list(remaining):
                if item.score.bucket == source_bucket and len(fillers) < needed:
                    fillers.append(item)
                    remaining.remove(item)
            if len(fillers) == needed:
                break
        if fillers:
            selected[bucket].extend(fillers)
            source_names = sorted({item.score.bucket for item in fillers})
            notes[bucket] = (
                f"Filled {len(fillers)} slot(s) from "
                f"{'/'.join(source_names)} because fewer natural {bucket} roles "
                "were available."
            )
        if len(selected[bucket]) < per_bucket:
            shortage = per_bucket - len(selected[bucket])
            extra = remaining[:shortage]
            selected[bucket].extend(extra)
            remaining = remaining[shortage:]
            if extra:
                notes[bucket] = (
                    f"Filled {len(extra)} slot(s) from the closest remaining "
                    "bucket because this category was short."
                )
        if len(selected[bucket]) < per_bucket:
            notes[bucket] = (
                f"Only {len(selected[bucket])} qualifying roles were available; "
                f"{per_bucket - len(selected[bucket])} slot(s) remain unfilled."
            )

    # Avoid letting one famous company dominate when other qualifying roles exist.
    selected_ids = {
        item.job.id for roles in selected.values() for item in roles
    }
    pool = [item for item in eligible if item.job.id not in selected_ids]
    company_counts = Counter(
        item.job.company for roles in selected.values() for item in roles
    )
    for bucket in BUCKETS:
        for index, item in enumerate(list(selected[bucket])):
            if company_counts[item.job.company] <= 2:
                continue
            candidates = [
                candidate
                for candidate in pool
                if company_counts[candidate.job.company] < 2
            ]
            if not candidates:
                continue
            candidates.sort(
                key=lambda candidate: (
                    candidate.score.bucket == bucket,
                    candidate.score.overall,
                ),
                reverse=True,
            )
            replacement = candidates[0]
            selected[bucket][index] = replacement
            pool.remove(replacement)
            pool.append(item)
            company_counts[item.job.company] -= 1
            company_counts[replacement.job.company] += 1
            note = (
                f"Included {replacement.job.company} to preserve company/source "
                "diversity instead of over-concentrating famous firms."
            )
            notes[bucket] = f"{notes[bucket]} {note}".strip()

    return {
        bucket: BucketSelection(tuple(selected[bucket]), notes[bucket])
        for bucket in BUCKETS
    }


def _score_line(item: ScoredJob) -> str:
    score = item.score
    return (
        f"**Overall {score.overall:.2f}/10** - "
        f"Skill {score.skill_fit:.1f} | Learning {score.learning_value:.1f} | "
        f"Accessibility {score.accessibility:.1f} | Timing {score.timing_fit:.1f} | "
        f"Location {score.location_fit:.1f}"
    )


def _bucket_reason(item: ScoredJob, bucket: str) -> str:
    if bucket == "Reach":
        return (
            "The technical content is unusually valuable, but the research, "
            "systems, quant, or company-level competition makes admission difficult."
        )
    if bucket == "Safe":
        return (
            "The role has a lower stated experience barrier while retaining useful "
            "Python, SQL, statistics, risk, or operations exposure."
        )
    return (
        "The role is a strong applied fit with a plausible undergraduate hiring "
        "bar after focused preparation."
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
            "Overall score = skill fit 30% + learning value 25% + accessibility "
            "20% + timing 15% + location 5% + company/career value 5%. Company "
            "brand is deliberately a small factor.",
            "",
        ]
    )
    return "\n".join(lines)
