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


def _location_assessment(job: Job, profile: dict[str, Any]) -> tuple[bool, float]:
    location = " ".join((*job.location_path, job.location)).lower()
    excluded = [
        str(country).lower()
        for country in profile["scope"].get("excluded_countries", [])
    ]
    china_terms = ("china", "beijing", "shanghai", "shenzhen", "hong kong")
    if any(term in location for term in (*excluded, *china_terms)):
        return False, 0.0
    if _contains(location, ("remote us", "remote - us", "us-remote", "united states")):
        return True, 9.2
    if _contains(location, ("united states", " usa", "u.s.")) or re.search(
        r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|"
        r"MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|"
        r"SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
        job.location,
        re.IGNORECASE,
    ):
        safe_cities = [
            str(city).lower()
            for city in profile["scope"].get("safe_livable_cities", [])
        ]
        return True, 9.0 if any(city in location for city in safe_cities) else 8.0
    if _contains(location, ("remote", "hybrid")):
        return True, 7.0
    if not location.strip():
        return True, 5.0
    return True, 5.5


def geography_allowed(job: Job, profile: dict[str, Any]) -> bool:
    return _location_assessment(job, profile)[0]


def _timing_score(job: Job, profile: dict[str, Any]) -> tuple[float, str]:
    title = job.title.lower()
    text = f"{job.title} {job.description}".lower()
    normalized_period = job.start_year_or_season.lower()
    period_years = set(re.findall(r"\b20\d{2}\b", normalized_period))
    title_years = set(re.findall(r"\b20\d{2}\b", title))
    contextual_years = set(
        re.findall(
            r"(?:start|summer|spring|winter|fall|internship|co-op)[^\n.]{0,24}\b(20\d{2})\b",
            text,
        )
    )
    years = title_years or period_years or contextual_years
    target_year = str(profile["scope"]["start_date"])[:4]
    if years and target_year not in years:
        return 2.0, f"Advertised for {'/'.join(sorted(years))}, not the 2027 window"
    if target_year in years:
        if _contains(text, ("fall 2027", "autumn 2027", "september 2027")):
            return 3.0, "Starts at or after graduate school begins"
        if _contains(text, ("summer", "july", "august")):
            return 7.5, "Summer 2027; confirm it ends before graduate school"
        if _contains(
            text,
            (
                "winter",
                "spring",
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "co-op",
                "fixed-term",
            ),
        ):
            return 10.0, ""
        return 8.5, ""
    early = _contains(
        f"{job.title} {job.employment_type}".lower(),
        ("intern", "co-op", "fixed-term", "contract", "graduate", "new grad"),
    )
    return (7.5, "Start date is not stated; verify Jan-Jun 2027") if early else (
        5.0,
        "Start date and term are not stated",
    )


def _career_value(job: Job, text: str) -> float:
    values = {
        "Big tech / famous lab": 8.2,
        "Mid-size tech": 7.8,
        "Startup": 7.4,
        "Insurance/risk": 7.3,
        "Healthcare analytics": 7.5,
        "Research/policy": 7.8,
        "Finance/market data": 7.6,
        "Logistics/OR": 7.5,
    }
    value = values.get(job.company_size_category, 7.0)
    if _contains(text, ("mentor", "mentorship", "paired with", "research team")):
        value += 0.8
    if _contains(text, ("own a project", "project ownership", "end-to-end")):
        value += 0.6
    return _clamp(value)


def _bucket_for(
    job: Job,
    text: str,
    accessibility: float,
    competitiveness: str,
) -> str:
    advanced = job.role_family in {
        "Machine Learning / AI",
        "Research",
        "Quant / Risk",
        "Data Infrastructure",
    } and _contains(
        text,
        (
            "foundation model",
            "large language model",
            "llm",
            "research scientist",
            "quantitative researcher",
            "distributed training",
            "ml infrastructure",
            "cuda",
        ),
    )
    technical_family = job.role_family in {
        "Machine Learning / AI",
        "Research",
        "Quant / Risk",
        "Data Infrastructure",
    } or _contains(
        text,
        (
            "machine learning",
            "data scientist",
            "quantitative",
            "research",
            "optimization",
        ),
    )
    famous_and_technical = (
        job.company_size_category == "Big tech / famous lab"
        and technical_family
    )
    if advanced or famous_and_technical or (
        competitiveness == "High" and accessibility < 7.5
    ):
        return "Reach"
    accessible_family = job.role_family in {
        "Analytics",
        "Quant / Risk",
        "Optimization / OR",
    }
    accessible_title = _contains(
        job.title.lower(),
        (
            "data analyst",
            "business analyst",
            "risk analyst",
            "operations analyst",
            "product analyst",
            "research assistant",
            "actuarial",
        ),
    )
    if accessibility >= 7.0 and (accessible_family or accessible_title):
        return "Safe"
    return "Target"


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
        "forecasting",
        "actuarial",
        "risk analytics",
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
        "data analyst",
        "data mining",
        "sql",
        "python",
        "analytics",
        "data pipeline",
        "large-scale data",
        "business analyst",
        "product analyst",
    )
    senior = _contains(
        title,
        (
            "senior",
            "sr.",
            "sr ",
            "staff",
            "principal",
            "lead ",
            "manager",
            "director",
            "head of",
            "expert",
        ),
    )

    if _contains(text, ml_terms):
        fit += 2.4
        learning += 2.4
        matches.append("Direct ML/algorithmic work matches Bobby's research direction")
    if _contains(text, math_terms):
        fit += 2.0
        learning += 1.7
        matches.append("Uses mathematical modeling, statistics, risk, or optimization")
    if _contains(text, experiment_terms):
        fit += 1.4
        learning += 1.8
        matches.append("Offers experimentation or research exposure")
    if _contains(text, data_terms):
        fit += 1.5
        learning += 1.2
        matches.append("Builds on Python, SQL, and data-analysis experience")
    if "python" in text:
        fit += 0.8
    if _contains(text, ("pytorch", "tensorflow", "jax")):
        learning += 0.8
        concerns.append("Deep-learning framework evidence should be clearer")
    if _contains(text, ("distributed", "production", "data pipeline", "large-scale")):
        learning += 1.0
        concerns.append("Production-scale systems experience may be a gap")

    early = _contains(
        f"{title} {job.employment_type}".lower(),
        (
            "intern",
            "graduate",
            "new grad",
            "early career",
            "co-op",
            "fixed-term",
            "contract",
            "student",
        ),
    )
    if early:
        access += 4.5
        matches.append("Explicitly structured as a student or early-career role")
    accessible_title = _contains(
        title,
        (
            "analyst",
            "associate ",
            "junior",
            "entry level",
            "entry-level",
            "research assistant",
        ),
    ) or bool(re.search(r"\bengineer i\b", title))
    if accessible_title and not senior:
        access += 3.2
        matches.append("The title signals an accessible analyst or associate level")
    if _contains(text, ("bachelor", "undergraduate", "b.s.", "bs degree")):
        access += 1.5
    if "preferred" in job.requirement.lower():
        access += 0.4

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

    phd_only = bool(re.search(r"\bph\.?d\b", title)) or _contains(
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

    low_value = _contains(
        text,
        (
            "data entry",
            "dashboard maintenance",
            "account executive",
            "sales representative",
            "marketing manager",
            "human resources",
            "recruiter",
            "administrative assistant",
        ),
    ) or (
        _contains(title, ("marketing", "sales", "recruiter"))
        and not _contains(
            title,
            (
                "data scientist",
                "data analyst",
                "machine learning",
                "quantitative",
            ),
        )
    )
    if low_value:
        fit -= 4.5
        learning -= 3.5
        concerns.append("The work is outside the target ML/data/quant path")

    geo_ok, location_fit = _location_assessment(job, profile)
    if not geo_ok:
        access -= 4.0
        concerns.append("China-based roles are excluded for now")

    timing_fit, timing_concern = _timing_score(job, profile)
    if timing_concern:
        concerns.append(timing_concern)
    if timing_fit <= 3.0:
        access -= 1.5

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
    career_value = _career_value(job, text)
    overall = round(
        0.30 * skill_fit
        + 0.25 * learning_value
        + 0.20 * accessibility
        + 0.15 * timing_fit
        + 0.05 * location_fit
        + 0.05 * career_value,
        2,
    )

    target_signal = _contains(text, ml_terms + math_terms + data_terms)
    relevant = bool(target_signal and not low_value and geo_ok)
    if not relevant:
        if not geo_ok:
            reason = "China-based role excluded by current search constraints"
        elif low_value:
            reason = "Low relevance to ML, data science, applied math, or quant work"
        else:
            reason = "Insufficient ML, data, mathematical, or quantitative content"
    elif phd_only:
        reason = "Interesting research content, but the role is PhD-targeted"
    elif senior or years >= 5:
        reason = "Relevant content, but the hiring level is not realistic now"
    elif timing_fit <= 3.0:
        reason = timing_concern
    elif accessibility < 5.0:
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
            "foundation model",
            "llm",
            "quantitative researcher",
        ),
    ):
        competition = "High"
    elif access >= 8.0 and job.company_size_category != "Big tech / famous lab":
        competition = "Low"
    else:
        competition = "Medium"

    bucket = _bucket_for(job, text, accessibility, competition)
    if not matches:
        matches.append("Some adjacent analytical content, but the fit is limited")
    if not concerns:
        concerns.append("Interview preparation and evidence of applied work are still needed")

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
        timing_fit=_clamp(timing_fit),
        location_fit=_clamp(location_fit),
        career_value=career_value,
        bucket=bucket,
    )
