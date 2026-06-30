from __future__ import annotations

from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import ScoredJob


def _score_line(item: ScoredJob) -> str:
    score = item.score
    return (
        f"**Overall {score.overall:.2f}/10** "
        f"(skill {score.skill_fit:.1f}, learning {score.learning_value:.1f}, "
        f"accessibility {score.accessibility:.1f})"
    )


def _job_block(item: ScoredJob, urgent_threshold: float) -> list[str]:
    job, score = item.job, item.score
    badges: list[str] = []
    if item.is_new:
        badges.append("NEW")
    if score.overall >= urgent_threshold:
        badges.append("URGENT APPLY")
    suffix = f" - {' / '.join(badges)}" if badges else ""
    lines = [
        f"### [{job.title}]({job.url}){suffix}",
        "",
        f"{job.company} - {job.location} - {job.recruitment_type or 'Type not listed'}",
        "",
        _score_line(item),
        "",
        f"**Competitiveness:** {score.competitiveness}",
        "",
        "**Why it matches Bobby:** " + "; ".join(score.why_match) + ".",
        "",
        "**Why it might not be a good idea:** " + "; ".join(score.concerns) + ".",
        "",
    ]
    if job.expires_at:
        lines.extend(
            [
                f"**Listed expiry:** {job.expires_at.date().isoformat()}",
                "",
            ]
        )
    return lines


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
    if "distributed" in text or "large-scale" in text:
        signals.append(
            "Build one production-flavored ML project with a data pipeline, "
            "evaluation layer, and documented scale tradeoffs."
        )
    if "recommendation" in text or "ranking" in text:
        signals.append(
            "Recommendation, ranking, and experimentation are recurring role "
            "clusters worth targeting."
        )
    return signals[:3] or [
        "Strengthen PyTorch and applied statistics while preserving the unusual "
        "math/ML-research positioning."
    ]


def build_report(
    scored: list[ScoredJob],
    profile: dict,
    generated_at: datetime,
) -> str:
    thresholds = profile["thresholds"]
    urgent = float(thresholds["urgent_apply"])
    top_floor = float(thresholds["top_opportunity"])
    rejected_floor = float(thresholds["interesting_reject"])

    ranked = sorted(scored, key=lambda item: item.score.overall, reverse=True)
    top = [
        item
        for item in ranked
        if item.score.relevant
        and not item.score.rejection_reason
        and item.score.overall >= top_floor
    ][: int(thresholds["max_top"])]
    rejected = [
        item
        for item in ranked
        if item.score.overall >= rejected_floor
        and item not in top
        and bool(item.score.rejection_reason)
        and item.score.relevant
    ][: int(thresholds["max_rejected"])]

    lines = [
        "# ByteDance Opportunity Intelligence Report",
        "",
        (
            "Generated "
            f"{generated_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M %Z')}"
        ),
        "",
        (
            f"Reviewed {len(scored)} unique roles. "
            f"Found {len(top)} top opportunities and {len(rejected)} "
            "rejected-but-interesting roles."
        ),
        "",
        "## A. Top Opportunities",
        "",
    ]
    if top:
        for item in top:
            lines.extend(_job_block(item, urgent))
    else:
        lines.extend(
            [
                "No role cleared the strict quality and realism threshold today. "
                "That is a valid result; the agent will not manufacture a match.",
                "",
            ]
        )

    lines.extend(["## B. Rejected But Interesting", ""])
    if rejected:
        for item in rejected:
            lines.extend(
                [
                    f"### [{item.job.title}]({item.job.url})",
                    "",
                    f"{item.job.location} - {_score_line(item)}",
                    "",
                    f"**Rejection reason:** "
                    f"{item.score.rejection_reason or '; '.join(item.score.concerns)}.",
                    "",
                ]
            )
    else:
        lines.extend(["No near-misses worth monitoring today.", ""])

    lines.extend(["## C. Strategy Advice", ""])
    for signal in _market_signals(ranked[:20]):
        lines.append(f"- {signal}")
    lines.extend(
        [
            "",
            "## Method",
            "",
            "Overall score = learning value 40% + skill fit 35% + "
            "accessibility 25%. Scores are deterministic screening aids, not "
            "promises of interview probability.",
            "",
        ]
    )
    return "\n".join(lines)
