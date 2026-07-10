from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Job


@dataclass(frozen=True)
class TimingClassification:
    tier: str
    score: float
    reason: str
    confidence: str
    hard_reject: bool = False


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def classify_timing(job: Job) -> TimingClassification:
    """Classify internship timing without rejecting vague but plausible roles."""
    text = job.text
    period = job.start_year_or_season.lower()
    combined = f"{period} {text}"
    years = set(re.findall(r"\b20\d{2}\b", combined))

    if _contains(combined, ("fall 2027", "autumn 2027")):
        return TimingClassification(
            "Hard reject",
            1.0,
            "Starts in Fall 2027 after graduate school begins",
            "High",
            True,
        )
    if "2027" in years and _contains(
        combined,
        ("spring", "winter", "jan", "january", "feb", "march", "apr", "may", "jun"),
    ):
        return TimingClassification(
            "A",
            10.0,
            "Explicitly aligns with Winter/Spring or Jan-Jun 2027",
            "High",
        )
    if "2027" in years and _contains(
        combined,
        ("co-op", "coop", "6 month", "six month", "3-6 month", "3 to 6 month"),
    ):
        return TimingClassification(
            "A",
            9.2,
            "2027 co-op or multi-month internship likely before graduate school",
            "Medium",
        )
    if "2027" in years and "summer" in combined:
        return TimingClassification(
            "B",
            7.6,
            "Summer 2027; confirm it ends before graduate school",
            "Medium",
        )
    if "2027" in years:
        return TimingClassification(
            "A",
            9.0,
            "Explicitly references 2027 and does not start after graduate school",
            "Medium",
        )
    if _contains(combined, ("summer 2026",)) and "2027" not in years:
        return TimingClassification(
            "Hard reject",
            1.0,
            "Explicitly Summer 2026 only; target window has passed",
            "High",
            True,
        )
    if "2028" in years or "2029" in years:
        return TimingClassification(
            "Hard reject",
            1.0,
            "Explicitly outside the target internship window",
            "High",
            True,
        )
    if _contains(combined, ("spring 2026", "winter 2026")) and "2027" not in years:
        return TimingClassification(
            "Hard reject",
            1.0,
            "Explicitly early 2026 only; target window has passed",
            "High",
            True,
        )
    if _contains(combined, ("fall 2026", "autumn 2026", "late 2026")):
        return TimingClassification(
            "C",
            5.6,
            "Late 2026 timing may extend toward 2027; verify dates",
            "Medium",
        )
    if _contains(
        text,
        (
            "rolling",
            "flexible start",
            "flexible",
            "off-cycle",
            "off cycle",
            "3-6 month",
            "3 to 6 month",
            "six month",
            "6 month",
            "co-op",
            "coop",
        ),
    ):
        return TimingClassification(
            "B",
            7.2,
            "Flexible, rolling, off-cycle, co-op, or 3-6 month timing",
            "Low",
        )
    if not years:
        return TimingClassification(
            "B",
            7.0,
            "Start date not stated; verify Jan-Jun 2027 availability",
            "Low",
        )
    return TimingClassification(
        "C",
        5.0,
        "Timing is ambiguous and cannot be reliably judged from the posting",
        "Low",
    )
