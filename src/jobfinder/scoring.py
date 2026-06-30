from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import Job, Score


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _clamp(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 1)


def _years_required(text: str) -> int:
    patterns = (
        r"(\d+)\+?\s+years?",
        r"minimum of\s+(\d+)\s+years?",
        r"at least\s+(\d+)\s+years?",
    )
    matches: list[int] = []
    for pattern in patterns:
        matches.extend(int(value) for value in re.findall(pattern, text))
    return max(matches, default=0)


def geography_allowed(job: Job, profile: dict[str, Any]) -> bool:
    location = " ".join(job.location_path).lower()
    if not location:
        return False
    regions = profile["scope"]["regions"]
    if _contains(location, ("united states", "usa", "u.s.")):
        return True
    if _contains(location, ("china", "hong kong")):
        allowed = [city.lower() for city in regions["China"]]
        return any(city in location for city in allowed)
    allowed_europe = [city.lower() for city in regions["Europe"]]
    return any(city in location for city in allowed_europe)


def score_job(job: Job, profile: dict[str, Any]) -> Score:
    text = job.text
    title = job.title.lower()
    fit = 1.5
    learning = 1.5
    access = 2.0
    matches: list[str] = []
    concerns: list[str] = []

    ml_terms = (
        "machine learning",
        "deep learning",
        "neural network",
        "artificial intelligence",
        " ai ",
        "llm",
        "recommendation",
        "ranking",
        "nlp",
        "computer vision",
        "机器学习",
        "算法",
    )
    math_terms = (
        "statistics",
        "probability",
        "linear algebra",
        "mathematical",
        "optimization",
        "inference",
        "causal",
        "modeling",
        "modelling",
        "quantitative",
        "operations research",
    )
    experiment_terms = (
        "experiment",
        "a/b test",
        "hypothesis",
        "evaluation",
        "simulation",
        "research",
    )
    data_terms = (
        "data science",
        "data scientist",
        "data mining",
        "sql",
        "python",
        "analytics",
        "data pipeline",
        "large-scale data",
    )

    if _contains(text, ml_terms):
        fit += 2.4
        learning += 2.4
        matches.append("Direct ML/algorithmic work matches Bobby's research direction")
    if _contains(text, math_terms):
        fit += 2.0
        learning += 1.7
        matches.append("Uses mathematical modeling, statistics, or optimization")
    if _contains(text, experiment_terms):
        fit += 1.4
        learning += 1.8
        matches.append("Offers experimentation or research exposure")
    if _contains(text, data_terms):
        fit += 1.5
        learning += 1.2
        matches.append("Builds on Python/data-analysis experience")
    if "python" in text:
        fit += 0.8
    if _contains(text, ("pytorch", "tensorflow", "jax")):
        learning += 0.8
        concerns.append("Deep-learning framework evidence should be clearer on the resume")
    if _contains(text, ("distributed", "production", "data pipeline", "large-scale")):
        learning += 1.0
        concerns.append("Production-scale systems experience may be a gap")

    early = _contains(
        title + " " + job.recruitment_type.lower(),
        ("intern", "graduate", "new grad", "campus", "early career", "实习", "校招"),
    )
    if early:
        access += 4.5
        matches.append("Explicitly targeted to interns, graduates, or early-career hires")
    if _contains(text, ("bachelor", "undergraduate", "b.s.", "bs degree")):
        access += 1.5
    if "preferred" in job.requirement.lower():
        access += 0.4

    senior = _contains(
        title,
        (
            "senior",
            "staff",
            "principal",
            "lead ",
            "manager",
            "director",
            "head of",
            "expert",
        ),
    )
    if senior:
        access -= 5.0
        concerns.append("The title signals a senior-level hiring bar")

    years = _years_required(text)
    if years >= 5:
        access -= 5.0
        concerns.append(f"Requires roughly {years}+ years of experience")
    elif years >= 3:
        access -= 3.0
        concerns.append(f"Requires roughly {years}+ years of experience")
    elif years >= 1:
        access -= 0.8

    phd_only = "(phd" in title or _contains(
        text,
        (
            "phd required",
            "ph.d. required",
            "doctoral degree required",
            "final year or recent phd",
            "current phd",
            "phd student",
            "ph.d. student",
        ),
    )
    if phd_only:
        access -= 6.0
        concerns.append("The role is PhD-targeted")

    target_year = str(profile["scope"]["start_date"])[:4]
    listed_years = set(re.findall(r"\b20\d{2}\b", title))
    timeline_mismatch = bool(listed_years and target_year not in listed_years)
    if timeline_mismatch:
        access -= 1.5
        concerns.append(
            f"Advertised for {'/'.join(sorted(listed_years))}, not the target "
            f"{target_year} start window"
        )

    low_value = _contains(
        text,
        (
            "data entry",
            "dashboard maintenance",
            "sales",
            "account executive",
            "marketing",
            "human resources",
            "recruiter",
            "administrative assistant",
        ),
    )
    if low_value:
        fit -= 4.5
        learning -= 3.5
        concerns.append("The work is outside the target ML/data/quant path")

    geo_ok = geography_allowed(job, profile)
    if not geo_ok:
        access -= 3.0
        concerns.append("Outside the Phase 1 target geographies")

    expires = job.expires_at
    if expires:
        days_left = (expires - datetime.now(timezone.utc)).days
        if days_left < 0:
            access = 0
            concerns.append("The listed expiry date has passed")
        elif days_left <= 7:
            concerns.append(f"Closing soon ({max(days_left, 0)} days remaining)")

    skill_fit = _clamp(fit)
    learning_value = _clamp(learning)
    accessibility = _clamp(access)
    overall = round(
        0.40 * learning_value + 0.35 * skill_fit + 0.25 * accessibility, 2
    )

    target_signal = _contains(text, ml_terms + math_terms + data_terms)
    relevant = bool(target_signal and not low_value and geo_ok)
    if not relevant:
        if not geo_ok:
            reason = "Outside the requested US, China-hub, and Europe-hub geography"
        elif low_value:
            reason = "Low relevance to ML, data science, applied math, or quant work"
        else:
            reason = "Insufficient ML, data, mathematical, or quantitative content"
    elif phd_only:
        reason = "Interesting research content, but the role is PhD-targeted"
    elif senior or years >= 5:
        reason = "Relevant content, but the hiring level is not realistic now"
    elif timeline_mismatch:
        reason = (
            f"Relevant content, but advertised for {'/'.join(sorted(listed_years))} "
            f"rather than the target {target_year} start window"
        )
    elif accessibility < 5.5:
        reason = (
            "Relevant content, but the posting does not signal realistic "
            "intern or new-grad accessibility"
        )
    else:
        reason = ""

    if access <= 3.0 or phd_only or senior:
        competition = "High"
    elif _contains(
        text,
        (
            "research scientist",
            "student researcher",
            "foundation model",
            "llm",
            "large language model",
            "seed",
        ),
    ):
        competition = "High"
    elif access >= 7.5 and overall < 8.3:
        competition = "Medium"
    else:
        competition = "Medium"

    if not matches:
        matches.append("Some adjacent analytical content, but the fit is limited")
    if not concerns:
        concerns.append("ByteDance roles are competitive even when the formal fit is good")

    return Score(
        skill_fit=skill_fit,
        learning_value=learning_value,
        accessibility=accessibility,
        overall=overall,
        relevant=relevant,
        geography_ok=geo_ok,
        why_match=tuple(dict.fromkeys(matches))[:3],
        concerns=tuple(dict.fromkeys(concerns))[:3],
        rejection_reason=reason,
        competitiveness=competition,
    )
