from __future__ import annotations

import re
from typing import Any, Iterable

from .models import Job, Score


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _clamp(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 1)


def _years_required(text: str) -> int:
    matches: list[int] = []
    for pattern in (
        r"(\d+)\+?\s+years?",
        r"minimum of\s+(\d+)\s+years?",
        r"at least\s+(\d+)\s+years?",
    ):
        matches.extend(int(value) for value in re.findall(pattern, text))
    return max(matches, default=0)


def company_size_group(job: Job) -> str:
    if job.company_size_category == "Big tech / famous lab":
        return "Large"
    if job.company_size_category == "Startup":
        return "Small"
    return "Mid"


def is_us_location(job: Job) -> bool:
    location = " ".join((*job.location_path, job.location)).lower()
    if job.country.lower() in {"united states", "united states of america", "usa"}:
        return True
    if _contains(
        location,
        (
            "united states",
            " usa",
            "u.s.",
            "us-remote",
            "remote us",
            "remote - us",
            "u.s. remote",
        ),
    ):
        return True
    if _contains(
        location,
        (
            "canada",
            "china",
            "hong kong",
            "singapore",
            "india",
            "united kingdom",
            "london",
            "france",
            "germany",
            "australia",
            "korea",
            "qatar",
        ),
    ):
        return False
    return bool(
        re.search(
            r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|"
            r"ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|"
            r"RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
            job.location,
            re.IGNORECASE,
        )
        or re.search(
            r"\bUS\s+(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|"
            r"KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|"
            r"OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
            job.location,
            re.IGNORECASE,
        )
        or re.search(
            r",\s*(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|"
            r"Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|"
            r"Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|"
            r"Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|"
            r"New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|"
            r"Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|"
            r"Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|"
            r"Wisconsin|Wyoming|District of Columbia)\b",
            job.location,
            re.IGNORECASE,
        )
    )


def _internship_clarity(job: Job) -> float:
    title = job.title.lower()
    employment = job.employment_type.lower().strip()
    title_explicit = bool(re.search(r"\b(intern|internship)\b", title))
    type_explicit = bool(re.search(r"\b(intern|internship)\b", employment))
    if title_explicit and type_explicit:
        return 10.0
    if title_explicit:
        return 9.5
    if type_explicit:
        return 9.0
    return 0.0


def is_internship_role(job: Job) -> bool:
    title = job.title.lower()
    employment = job.employment_type.lower().strip()
    if _contains(title, ("new grad", "new graduate", "graduate role")):
        return False
    if _contains(employment, ("full time", "full-time", "fulltime", "regular")):
        return False
    if "return offer" in title and not re.search(r"\b(intern|internship)\b", title):
        return False
    return _internship_clarity(job) > 0


def is_us_internship(job: Job) -> bool:
    return is_us_location(job) and is_internship_role(job)


def geography_allowed(job: Job, profile: dict[str, Any]) -> bool:
    return is_us_location(job)


def _timing_fit(job: Job, profile: dict[str, Any]) -> tuple[float, str]:
    period = job.start_year_or_season.lower()
    years = set(re.findall(r"\b20\d{2}\b", period))
    if years and "2027" not in years:
        return 1.0, f"Advertised for {'/'.join(sorted(years))}, not 2027"
    if "fall 2027" in period or "autumn 2027" in period:
        return 2.0, "Starts when graduate school begins"
    if "2027" in years and "summer" in period:
        return 7.5, "Confirm Summer 2027 ends before graduate school"
    if "2027" in years:
        return 10.0, ""
    return 7.0, "Start date is not stated; verify Jan-Jun 2027"


def _relevance(job: Job) -> tuple[float, list[str]]:
    text = job.text
    title = job.title.lower()
    score = 1.5
    reasons: list[str] = []
    if _contains(
        text,
        (
            "analytics",
            "data analyst",
            "business analyst",
            "product analyst",
            "decision science",
            "sql",
            "python",
        ),
    ):
        score += 3.0
        reasons.append("Uses Python, SQL, analytics, or data-driven decision making")
    if _contains(
        text,
        (
            "statistics",
            "probability",
            "forecasting",
            "risk",
            "quantitative",
            "economics",
            "finance",
            "actuarial",
        ),
    ):
        score += 2.5
        reasons.append("Aligns with Bobby's math, economics, statistics, or risk background")
    if _contains(
        text,
        (
            "operations research",
            "operations",
            "optimization",
            "supply chain",
            "simulation",
            "experimentation",
            "a/b test",
        ),
    ):
        score += 2.0
        reasons.append("Offers modeling, optimization, or experimentation work")
    if _contains(
        text,
        (
            "machine learning",
            "data science",
            "algorithm",
        ),
    ):
        score += 1.5
        reasons.append("Builds technical ML, data science, or software experience")
    if _contains(
        title,
        (
            "software engineer",
            "software developer",
            "data engineer",
        ),
    ):
        score += 2.5
        reasons.append("Builds technical ML, data science, or software experience")
    if _contains(
        title,
        (
            "operations",
            "supply chain",
            "logistics",
        ),
    ):
        score += 1.0
        reasons.append("Offers modeling, optimization, or experimentation work")
    return _clamp(score), reasons


def _competition_ease(job: Job, text: str) -> tuple[float, float]:
    size = company_size_group(job)
    ease = {"Large": 3.5, "Mid": 7.0, "Small": 9.0}[size]
    popularity_penalty = {"Large": 1.2, "Mid": 0.35, "Small": 0.0}[size]
    if _contains(
        text,
        (
            "foundation model",
            "research scientist",
            "quantitative researcher",
            "publication",
            "top-tier conference",
            "distributed training",
            "cuda",
        ),
    ):
        ease -= 2.0
        popularity_penalty += 0.6
    if _contains(job.title.lower(), ("software engineer", "machine learning intern")):
        ease -= 0.8
        popularity_penalty += 0.3
    return _clamp(ease), round(popularity_penalty, 2)


def _requirement_ease(job: Job, text: str) -> tuple[float, list[str], bool]:
    ease = 9.0
    concerns: list[str] = []
    phd_mentioned = bool(re.search(r"\bph\.?d\b", text)) or "doctoral" in text
    phd_in_title = bool(
        re.search(r"\bph\.?d\b", job.title, re.IGNORECASE)
        or "doctoral" in job.title.lower()
    )
    undergraduate_path = _contains(
        text,
        (
            "bachelor",
            "undergraduate",
            "undergrad",
            "bs/ms",
            "b.s.",
            "pursuing a degree",
            "currently enrolled",
        ),
    )
    phd_only = phd_in_title or (
        phd_mentioned
        and not undergraduate_path
        and _contains(
            text,
            (
                "research scientist",
                "doctoral candidate",
                "phd student",
                "ph.d. student",
                "pursuing a phd",
                "pursuing a ph.d",
            ),
        )
    ) or _contains(
        text,
        ("publication record required", "top-tier publications required"),
    )
    if phd_only:
        ease -= 6.0
        concerns.append("PhD or publication-heavy expectations sharply reduce accessibility")
    elif phd_mentioned:
        ease -= 0.5
    years = _years_required(text)
    if years >= 5:
        ease -= 6.0
        concerns.append(f"Requires roughly {years}+ years of experience")
    elif years >= 3:
        ease -= 4.0
        concerns.append(f"Requires roughly {years}+ years of experience")
    elif years >= 1:
        ease -= 1.2
    if _contains(
        text,
        ("distributed systems", "cuda", "production ml", "large-scale systems"),
    ):
        ease -= 1.5
        concerns.append("Advanced production or systems experience may be a gap")
    return _clamp(ease), concerns, phd_only


def _practical_value(job: Job, text: str) -> float:
    value = 6.0
    if _contains(
        text,
        (
            "python",
            "sql",
            "modeling",
            "forecasting",
            "experimentation",
            "optimization",
            "risk",
        ),
    ):
        value += 2.0
    if _contains(text, ("mentor", "mentorship", "project ownership", "end-to-end")):
        value += 1.0
    return _clamp(value)


def _bucket(
    job: Job,
    text: str,
    competition_ease: float,
    requirement_ease: float,
) -> str:
    ease_biased = _contains(
        job.title.lower(),
        (
            "analytics",
            "data analyst",
            "business analyst",
            "product analyst",
            "risk",
            "finance",
            "operations",
            "actuarial",
        ),
    )
    hard_research = _contains(
        text,
        (
            "foundation model",
            "research scientist",
            "publication",
            "quantitative researcher",
        ),
    )
    if hard_research or competition_ease <= 4.0 or requirement_ease <= 4.0:
        return "Reach"
    if ease_biased and competition_ease >= 6.0 and requirement_ease >= 6.0:
        return "Safe"
    return "Target"


def score_job(job: Job, profile: dict[str, Any]) -> Score:
    text = job.text
    clarity = _internship_clarity(job)
    us_eligible = is_us_location(job)
    internship_eligible = is_internship_role(job)
    timing_fit, timing_concern = _timing_fit(job, profile)
    relevance, matches = _relevance(job)
    competition_ease, popularity_penalty = _competition_ease(job, text)
    requirement_ease, concerns, phd_only = _requirement_ease(job, text)
    stability = 9.0 if "remote" in job.location.lower() else 10.0
    practical = _practical_value(job, text)

    low_value = _contains(
        job.title.lower(),
        (
            "marketing",
            "sales",
            "recruiter",
            "human resources",
            "administrative",
        ),
    )
    graduate_only = _contains(
        job.title.lower(),
        (
            "mba intern",
            "mba internship",
            "phd intern",
            "ph.d. intern",
            "doctoral intern",
        ),
    )
    overall = round(
        0.30 * relevance
        + 0.20 * clarity
        + 0.20 * competition_ease
        + 0.15 * requirement_ease
        + 0.10 * stability
        + 0.05 * practical
        - popularity_penalty,
        2,
    )
    overall = _clamp(overall)

    if not us_eligible:
        reason = "Not a clearly U.S.-based role"
    elif not internship_eligible:
        reason = "Not an explicit internship or is marked full-time/new-grad"
    elif graduate_only:
        reason = "Internship is restricted to MBA or doctoral candidates"
    elif low_value:
        reason = "Role is outside the target analytical/technical path"
    elif relevance < 4.0:
        reason = "Insufficient relevance to math, economics, data, OR, SWE, or analytics"
    elif phd_only and requirement_ease <= 3.0:
        reason = "Internship is too PhD/publication-heavy for the current profile"
    else:
        reason = ""

    relevant = not reason
    if timing_concern:
        concerns.append(timing_concern)
    if not concerns:
        concerns.append("Confirm project scope, mentorship, and interview expectations")
    if not matches:
        matches.append("Provides adjacent analytical or technical internship experience")

    if competition_ease <= 4.0:
        competitiveness = "High"
    elif competition_ease >= 7.5:
        competitiveness = "Low"
    else:
        competitiveness = "Medium"
    bucket = _bucket(job, text, competition_ease, requirement_ease)

    accessibility = _clamp(
        0.40 * competition_ease + 0.35 * requirement_ease + 0.25 * clarity
    )
    return Score(
        skill_fit=relevance,
        learning_value=practical,
        accessibility=accessibility,
        overall=overall,
        relevant=relevant,
        geography_ok=us_eligible,
        why_match=tuple(dict.fromkeys(matches))[:3],
        concerns=tuple(dict.fromkeys(concerns))[:3],
        rejection_reason=reason,
        competitiveness=competitiveness,
        timing_fit=timing_fit,
        location_fit=stability if us_eligible else 0.0,
        career_value=practical,
        bucket=bucket,
        internship_clarity=clarity,
        competition_ease=competition_ease,
        requirement_ease=requirement_ease,
        us_stability=stability if us_eligible else 0.0,
        practical_value=practical,
        popularity_penalty=popularity_penalty,
    )
