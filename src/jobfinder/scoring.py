from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .models import Job, Score
from .role_classification import classify_role
from .timing import classify_timing


AI_TITLE_TERMS = (
    "ai engineer",
    "applied ai engineer",
    "llm engineer",
    "agent engineer",
    "agentic",
    "agentic ai engineer",
    "generative ai engineer",
    "ai automation engineer",
    "ai solutions engineer",
    "ai product engineer",
    "research engineer",
)

AI_ENGINEERING_SIGNALS = (
    "llm",
    "large language model",
    "rag",
    "retrieval augmented",
    "embeddings",
    "vector database",
    "langchain",
    "autogen",
    "crewai",
    "openai api",
    "anthropic api",
    "fine tuning",
    "fine-tuning",
    "model evaluation",
    "ai agent",
    "ai agents",
    "agentic",
    "ai workflow automation",
    "workflow automation",
    "tool calling",
    "function calling",
    "prompt engineering",
)

AI_SYSTEM_BUILDING_TERMS = (
    "build",
    "building",
    "develop",
    "design",
    "implement",
    "ship",
    "prototype",
    "deploy",
    "evaluate",
    "integrate",
    "automate",
    "productionize",
    "own",
)

AI_ENGINEER_KEYWORDS = (
    *AI_TITLE_TERMS,
    *AI_ENGINEERING_SIGNALS,
    "generative ai",
    "genai",
    "n8n",
    "zapier",
    "ai automation",
    "openai",
    "anthropic",
    "hugging face",
    "applied ai",
    "ai product",
    "ai platform",
    "ai solutions",
)

PURE_SWE_TERMS = (
    "pure backend",
    "backend engineer",
    "frontend engineer",
    "front end engineer",
    "mobile engineer",
    "ios engineer",
    "android engineer",
    "devops",
    "site reliability",
    "infrastructure",
    "general software engineer",
    "software engineer intern",
    "forward deployed software engineer",
    "technical support engineer",
    "support engineering",
    "electrical engineer",
    "android",
)


@dataclass(frozen=True)
class AIEngineerClassification:
    is_ai_engineer: bool
    focus: str
    keywords: tuple[str, ...]
    reason: str
    pure_swe_signal: bool = False


@dataclass(frozen=True)
class CareerRelevance:
    ai: float
    optimization: float
    applied_math: float
    data: float
    quant: float
    total: float
    primary_track: str
    reasons: tuple[str, ...]
    keywords: tuple[str, ...]
    pure_swe_signal: bool = False
    business_dashboard_signal: bool = False


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _clamp(value: float) -> float:
    return round(max(0.0, min(10.0, value)), 1)


def _unique_matches(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term for term in terms if term in text))


def _score_terms(
    title: str,
    text: str,
    *,
    title_terms: Iterable[str],
    keywords: Iterable[str],
    title_points: float,
    keyword_points: float,
    cap: float,
) -> tuple[float, tuple[str, ...]]:
    title_matches = _unique_matches(title, title_terms)
    keyword_matches = _unique_matches(text, keywords)
    score = min(
        cap,
        title_points * len(title_matches) + keyword_points * len(keyword_matches),
    )
    return score, tuple(dict.fromkeys((*title_matches, *keyword_matches)))


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
    timing = classify_timing(job)
    concern = "" if timing.tier == "A" else timing.reason
    return timing.score, concern


def is_target_timing(job: Job, profile: dict[str, Any]) -> bool:
    return not classify_timing(job).hard_reject


def is_pure_swe_title(job: Job) -> bool:
    return classify_role(job).classification == "pure_swe"


def classify_career_relevance(job: Job) -> CareerRelevance:
    text = job.text
    title = job.title.lower()
    ai_classification = classify_ai_engineer(job)
    ai, ai_keywords = _score_terms(
        title,
        text,
        title_terms=(
            "ai engineer",
            "machine learning engineer",
            "ml engineer",
            "applied ai",
            "generative ai",
            "llm engineer",
            "ai research",
            "ai research intern",
            "model evaluation",
            "research engineer",
            "ml infrastructure",
            "ai infrastructure",
        ),
        keywords=(
            "llm",
            "rag",
            "ai agent",
            "ai agents",
            "langchain",
            "langgraph",
            "workflow automation",
            "model evaluation",
            "prompt engineering",
            "ml pipeline",
            "ml pipelines",
            "ml platform",
            "feature store",
            "deep learning",
            "nlp",
            "computer vision",
        ),
        title_points=12.0,
        keyword_points=4.0,
        cap=35.0,
    )
    if ai_classification.is_ai_engineer:
        ai = max(ai, 24.0)
        ai_keywords = tuple(dict.fromkeys((*ai_keywords, *ai_classification.keywords)))

    optimization, opt_keywords = _score_terms(
        title,
        text,
        title_terms=(
            "operations research",
            "optimization intern",
            "decision science",
            "decision support",
            "algorithm intern",
            "algorithm research",
            "simulation intern",
            "modeling and simulation",
            "supply chain optimization",
            "supply chain analytics",
            "data science optimization",
            "energy modeling",
            "grid analytics",
            "defense modeling",
            "network planning",
            "revenue management",
            "pricing analytics",
            "operations analytics",
        ),
        keywords=(
            "linear programming",
            "integer programming",
            "stochastic optimization",
            "simulation",
            "mathematical optimization",
            "forecasting",
            "decision models",
            "decision science",
            "decision support",
            "operations research",
            "operations analytics",
            "probability models",
            "network planning",
            "energy modeling",
            "grid analytics",
            "supply chain analytics",
            "optimization",
        ),
        title_points=10.0,
        keyword_points=3.2,
        cap=25.0,
    )
    applied_math, math_keywords = _score_terms(
        title,
        text,
        title_terms=(
            "applied mathematics",
            "applied mathematician",
            "computational mathematics",
            "scientific computing",
            "numerical analysis",
            "mathematical modeling",
            "computational modeling",
            "modeling and simulation",
            "simulation research",
            "computational science",
            "scientific machine learning",
        ),
        keywords=(
            "differential equations",
            "pde",
            "ode",
            "numerical methods",
            "computational modeling",
            "mathematical modeling",
            "simulation",
            "scientific computing",
            "scientific machine learning",
            "dynamical systems",
            "optimization theory",
        ),
        title_points=9.0,
        keyword_points=3.0,
        cap=20.0,
    )
    data, data_keywords = _score_terms(
        title,
        text,
        title_terms=(
            "data science intern",
            "statistical modeling",
            "machine learning data scientist",
            "healthcare analytics",
            "actuarial data analytics",
            "actuarial analytics",
            "quantitative analyst",
            "data analyst intern",
            "product analyst intern",
            "product analytics",
            "statistical research",
            "analytics scientist",
        ),
        keywords=(
            "python",
            "statistics",
            "machine learning",
            "data pipeline",
            "data pipelines",
            "feature store",
            "ml platform",
            "experimentation",
            "causal inference",
            "predictive modeling",
            "forecasting",
            "statistical research",
            "quantitative analysis",
            "product analytics",
            "optimization",
            "statistical",
            "modeling",
        ),
        title_points=6.0,
        keyword_points=1.8,
        cap=15.0,
    )
    quant, quant_keywords = _score_terms(
        title,
        text,
        title_terms=(
            "quant research",
            "quantitative research",
            "quantitative analyst",
            "quantitative analysis",
            "risk modeling",
            "financial modeling",
            "actuarial",
        ),
        keywords=(
            "probability",
            "stochastic processes",
            "statistics",
            "machine learning",
            "optimization",
            "forecasting",
            "risk modeling",
            "actuarial modeling",
            "risk",
        ),
        title_points=3.0,
        keyword_points=1.0,
        cap=5.0,
    )

    bonuses = 0.0
    reasons: list[str] = []
    if _contains(
        text,
        (
            "mathematics",
            "applied mathematics",
            "statistics",
            "computational science",
            "math major",
            "mathematics major",
        ),
    ):
        bonuses += 10.0
        reasons.append("Explicitly welcomes math/applied math/statistics/computational science background")
    if _contains(
        text,
        (
            "research",
            "modeling",
            "algorithm",
            "algorithms",
            "simulation",
            "numerical",
            "forecasting",
            "causal inference",
        ),
    ):
        bonuses += 10.0
        reasons.append("Involves research, modeling, algorithms, numerical work, or simulation")
    if company_size_group(job) in {"Mid", "Small"} and _contains(
        text,
        (
            "build",
            "own",
            "end-to-end",
            "prototype",
            "cross-functional",
            "research",
        ),
    ):
        bonuses += 5.0
        reasons.append("Smaller or mid-size environment with broader project ownership")
    if ai >= 12 or optimization >= 10 or applied_math >= 8:
        bonuses += 5.0
        reasons.append("Can lead toward AI Engineer, Applied Scientist, or Optimization career paths")

    pure_swe_signal = is_pure_swe_title(job)
    business_dashboard_signal = _contains(
        text,
        (
            "dashboard",
            "dashboards",
            "business analyst",
            "sales analytics",
            "marketing analytics",
            "reporting dashboard",
        ),
    ) and not _contains(
        text,
        (
            "modeling",
            "machine learning",
            "optimization",
            "research",
            "simulation",
            "predictive",
            "forecasting",
            "experimentation",
            "causal inference",
            "statistical",
            "quantitative",
        ),
    )
    penalties = 0.0
    if pure_swe_signal and ai < 10 and data < 8:
        penalties += 20.0
    if business_dashboard_signal:
        penalties += 20.0

    components = {
        "AI / Applied AI": ai,
        "Operations Research / Optimization": optimization,
        "Applied Math / Computational Math": applied_math,
        "Data Science / Statistics": data,
        "Quant / Risk Modeling": quant,
    }
    primary_track = max(components, key=components.get)
    total = max(0.0, ai + optimization + applied_math + data + quant + bonuses - penalties)
    keywords = tuple(
        dict.fromkeys(
            (
                *ai_keywords,
                *opt_keywords,
                *math_keywords,
                *data_keywords,
                *quant_keywords,
            )
        )
    )[:12]
    if components[primary_track] > 0:
        reasons.insert(0, f"Primary track: {primary_track}")
    return CareerRelevance(
        ai=round(ai, 1),
        optimization=round(optimization, 1),
        applied_math=round(applied_math, 1),
        data=round(data, 1),
        quant=round(quant, 1),
        total=round(total, 1),
        primary_track=primary_track if components[primary_track] > 0 else "Unclassified",
        reasons=tuple(dict.fromkeys(reasons))[:4],
        keywords=keywords,
        pure_swe_signal=pure_swe_signal,
        business_dashboard_signal=business_dashboard_signal,
    )


def _relevance(job: Job) -> tuple[float, list[str]]:
    relevance = classify_career_relevance(job)
    reasons = list(relevance.reasons)
    if relevance.keywords:
        reasons.append("Matched signals: " + ", ".join(relevance.keywords[:5]))
    return _clamp(relevance.total / 5.0), reasons


def classify_ai_engineer(job: Job) -> AIEngineerClassification:
    """Strict AI Engineer classifier used before ranking.

    A single AI token, especially a generic occurrence such as "RAG", is not
    enough. The title must be AI-engineering focused, or the description must
    contain multiple AI-engineering signals plus responsibility language that
    indicates building AI systems.
    """
    text = job.text
    title = job.title.lower()
    title_matches = _unique_matches(title, AI_TITLE_TERMS)
    signal_matches = _unique_matches(text, AI_ENGINEERING_SIGNALS)
    building_matches = _unique_matches(text, AI_SYSTEM_BUILDING_TERMS)
    pure_swe_signal = is_pure_swe_title(job)
    unrelated_title = _contains(
        title,
        (
            "project management",
            "technical support",
            "support engineering",
            "electrical engineer",
            "android",
            "facilities",
            "payment partnership",
            "operations intern",
        ),
    )
    if title_matches and not unrelated_title:
        reason = (
            "AI Engineer title match: "
            + ", ".join(title_matches[:3])
        )
        return AIEngineerClassification(
            True,
            "AI Engineer / Agentic AI",
            tuple(dict.fromkeys((*title_matches, *signal_matches)))[:8],
            reason,
            pure_swe_signal,
        )
    if len(signal_matches) >= 2 and building_matches and not unrelated_title:
        reason = (
            "Multiple AI-engineering signals with system-building responsibilities: "
            + ", ".join(signal_matches[:4])
        )
        return AIEngineerClassification(
            True,
            "AI Engineer / Agentic AI",
            signal_matches[:8],
            reason,
            pure_swe_signal,
        )
    if signal_matches:
        reason = (
            "AI keyword(s) found but not enough to classify as AI Engineer: "
            + ", ".join(signal_matches[:4])
        )
    elif pure_swe_signal:
        reason = "SWE/support/electrical/project title without major AI-engineering scope"
    else:
        reason = "No AI Engineer title or multi-signal AI-system building evidence"
    return AIEngineerClassification(
        False,
        "Not AI Engineer",
        signal_matches[:8],
        reason,
        pure_swe_signal,
    )


def _ai_focus(job: Job) -> tuple[float, tuple[str, ...], str, bool]:
    classification = classify_ai_engineer(job)
    if classification.is_ai_engineer:
        base = 8.0 + min(2.0, 0.4 * len(classification.keywords))
        if classification.pure_swe_signal:
            base -= 1.0
        return (
            _clamp(base),
            classification.keywords,
            classification.focus,
            classification.pure_swe_signal,
        )
    if classification.keywords:
        return (
            2.5,
            classification.keywords,
            "AI keyword present, but not AI Engineer",
            classification.pure_swe_signal,
        )
    return (
        1.0,
        tuple(),
        "Not AI Engineer",
        classification.pure_swe_signal,
    )


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
    if _contains(
        text,
        (
            "production software",
            "production systems",
            "microservices",
            "frontend",
            "front-end",
            "backend",
            "mobile app",
            "on-call",
        ),
    ):
        ease -= 0.7
        popularity_penalty += 0.2
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


def _work_authorization_status(text: str, profile: dict[str, Any]) -> tuple[str, str]:
    us_citizen = bool(profile.get("us_citizen"))
    active_clearance = bool(profile.get("active_clearance"))
    clearance_eligible = bool(profile.get("willing_eligible_clearance"))
    citizenship_required = _contains(
        text,
        (
            "must be a u.s. citizen",
            "must be a us citizen",
            "u.s. citizenship is required",
            "us citizenship is required",
            "u.s. citizen required",
            "us citizen required",
            "requires u.s. citizenship",
            "requires us citizenship",
        ),
    )
    active_clearance_required = _contains(
        text,
        (
            "active security clearance required",
            "requires an active security clearance",
            "current security clearance required",
            "existing security clearance required",
            "must possess an active clearance",
            "must have an active clearance",
            "active secret clearance",
            "active top secret clearance",
            "active ts/sci",
            "current secret clearance",
            "current top secret clearance",
        ),
    )
    clearance_ability_required = _contains(
        text,
        (
            "ability to obtain a security clearance",
            "able to obtain a security clearance",
            "eligible to obtain a security clearance",
            "must be able to obtain",
            "clearance eligibility",
        ),
    )
    if active_clearance_required and not active_clearance:
        return "blocked", "Requires active/current security clearance"
    if citizenship_required and not us_citizen:
        return "blocked", "Requires U.S. citizenship"
    if clearance_ability_required and not clearance_eligible:
        return "concern", "May require eligibility to obtain a security clearance"
    if clearance_ability_required:
        return "concern", "Security clearance eligibility should be confirmed"
    if citizenship_required:
        return "accepted", "U.S. citizenship requirement is satisfied"
    return "accepted", ""


def _degree_eligibility_status(job: Job, text: str) -> tuple[str, str]:
    title = job.title.lower()
    has_bachelor_path = _contains(
        text,
        (
            "bachelor",
            "b.s.",
            "bs/ms",
            "undergraduate",
            "undergrad",
            "currently enrolled in a degree",
            "pursuing a degree",
            "students pursuing a degree",
            "open to undergrad",
        ),
    )
    optional_or_preferred = _contains(
        text,
        (
            "preferred",
            "preference",
            "preferred qualifications",
            "nice to have",
            "or equivalent",
            "bachelor's, master's, or phd",
            "undergrad, master's, or phd",
            "bs/ms",
        ),
    )
    mba_only = (
        _contains(title, ("mba intern", "mba internship"))
        or bool(
            re.search(
                r"\b(?:currently\s+)?(?:pursuing|enrolled in|enrolled)\s+(?:an?\s+)?mba\b",
                text,
            )
        )
        or _contains(
            text,
            (
                "must be enrolled in an mba",
                "must be currently enrolled in an mba",
                "first-year mba",
                "rising second-year mba",
                "mba class 2027",
                "mba class of 2027",
                "mba class 2028",
                "mba class of 2028",
            ),
        )
    )
    jd_or_medical_only = bool(
        re.search(r"\b(?:jd|j\.d\.|law school|medical school|md student)\b", text)
    ) and not has_bachelor_path
    doctoral_only = _contains(
        title,
        ("phd intern", "ph.d. intern", "doctoral intern"),
    ) or _contains(
        text,
        (
            "doctoral candidates only",
            "doctoral candidate only",
            "phd students only",
            "ph.d. students only",
            "phd candidates only",
            "ph.d. candidates only",
            "must be enrolled in a phd",
            "must be enrolled in a ph.d",
        ),
    )
    masters_only = (
        _contains(
            text,
            (
                "graduate students only",
                "graduate student only",
                "must be enrolled in a master's",
                "must be enrolled in a masters",
                "must be enrolled in a master",
                "currently enrolled in a master's",
                "currently enrolled in a masters",
                "current master's student",
                "current masters student",
            ),
        )
        and not has_bachelor_path
    )
    if optional_or_preferred and has_bachelor_path:
        return "eligible", "Bachelor's/undergraduate path remains eligible"
    if mba_only:
        return "blocked", "MBA-only internship"
    if jd_or_medical_only:
        return "blocked", "JD/medical-program-only internship"
    if doctoral_only:
        return "blocked", "Doctoral/PhD-only internship"
    if masters_only:
        return "blocked", "Master's/graduate-students-only internship"
    return "eligible", ""


def _practical_value(job: Job, text: str) -> float:
    value = 6.0
    if _contains(
        text,
        (
            "python",
            "sql",
            "numpy",
            "scipy",
            "pandas",
            "modeling",
            "forecasting",
            "experimentation",
            "optimization",
            "risk",
            "llm",
            "rag",
            "workflow automation",
            "openai api",
            "model evaluation",
            "scientific computing",
            "numerical methods",
            "numerical analysis",
            "operations research",
            "simulation",
            "mathematical modeling",
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
            "ai engineer",
            "applied ai",
            "llm",
            "automation",
            "operations research",
            "optimization",
            "applied mathematics",
            "computational",
            "scientific computing",
            "data science",
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
    timing_classification = classify_timing(job)
    timing_fit, timing_concern = _timing_fit(job, profile)
    timing_eligible = not timing_classification.hard_reject
    role_classification = classify_role(job)
    ai_classification = classify_ai_engineer(job)
    career_relevance = classify_career_relevance(job)
    relevance, matches = _relevance(job)
    ai_score, ai_keywords, ai_focus, pure_swe_signal = _ai_focus(job)
    competition_ease, popularity_penalty = _competition_ease(job, text)
    requirement_ease, concerns, phd_only = _requirement_ease(job, text)
    authorization_status, authorization_reason = _work_authorization_status(
        text,
        profile,
    )
    degree_status, degree_reason = _degree_eligibility_status(job, text)
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
    title = job.title.lower()
    missing_detail = not (job.description.strip() or job.requirement.strip())
    plausible_title_only = missing_detail and _contains(
        title,
        (
            "data science intern",
            "data scientist intern",
            "machine learning intern",
            "ai intern",
            "analytics intern",
            "analyst intern",
            "operations research intern",
            "optimization intern",
            "quantitative analyst intern",
            "research intern",
        ),
    )
    overall = round(
        0.50 * relevance
        + 0.18 * clarity
        + 0.14 * competition_ease
        + 0.08 * requirement_ease
        + 0.03 * stability
        + 0.07 * practical
        - popularity_penalty,
        2,
    )
    if company_size_group(job) in {"Mid", "Small"} and career_relevance.total >= 25:
        overall += 0.3
    if ai_classification.is_ai_engineer:
        overall += 0.7
    if role_classification.classification == "pure_swe":
        overall -= 2.5
    elif role_classification.classification == "uncertain":
        overall -= 0.6
    if career_relevance.business_dashboard_signal:
        overall -= 2.0
    overall = _clamp(overall)

    if not us_eligible:
        reason = "Not a clearly U.S.-based role"
    elif not internship_eligible:
        reason = "Not an explicit internship or is marked full-time/new-grad"
    elif not timing_eligible:
        reason = timing_concern or "Internship timing is outside Spring/Summer 2027 target"
    elif authorization_status == "blocked":
        reason = authorization_reason
    elif degree_status == "blocked":
        reason = f"Internship is restricted to {degree_reason}"
    elif low_value:
        reason = "Role is outside the target analytical/technical path"
    elif career_relevance.business_dashboard_signal:
        reason = "Business analytics/dashboard-only role without modeling, ML, optimization, or research depth"
    elif role_classification.classification == "pure_swe" and career_relevance.total < 12.0:
        reason = "Pure SWE internship without AI/ML/optimization/modeling scope"
    elif career_relevance.total < 12.0 and not plausible_title_only:
        reason = "Insufficient relevance to AI, applied science, OR/optimization, applied math, modeling, data science, or quant/risk"
    elif phd_only and requirement_ease <= 3.0:
        reason = "Internship is too PhD/publication-heavy for the current profile"
    else:
        reason = ""

    relevant = not reason
    if authorization_reason and authorization_status != "blocked":
        concerns.append(authorization_reason)
    if plausible_title_only and relevant:
        concerns.append("Description or requirements missing; keep as low-confidence manual review")
    if timing_concern:
        concerns.append(timing_concern)
    if not concerns:
        concerns.append("Confirm project scope, mentorship, and interview expectations")
    if role_classification.classification == "pure_swe":
        concerns.insert(0, "Verify this is not a pure SWE role before applying")
    elif role_classification.classification == "uncertain":
        concerns.insert(0, "Role scope is ambiguous; manually verify AI/ML/modeling relevance")
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
        ai_focus=ai_focus,
        ai_keywords=tuple(dict.fromkeys((*ai_keywords, *career_relevance.keywords)))[:12],
        pure_swe_signal=pure_swe_signal,
        ai_engineer=ai_classification.is_ai_engineer,
        ai_classification_reason=ai_classification.reason,
        ai_relevance_score=career_relevance.ai,
        optimization_relevance_score=career_relevance.optimization,
        applied_math_relevance_score=career_relevance.applied_math,
        data_relevance_score=career_relevance.data,
        quant_relevance_score=career_relevance.quant,
        relevance_total=career_relevance.total,
        primary_track=career_relevance.primary_track,
        timing_tier=timing_classification.tier,
        timing_reason=timing_classification.reason,
        timing_confidence=timing_classification.confidence,
        role_classification=role_classification.classification,
        role_classification_reason=role_classification.reason,
        role_classification_confidence=role_classification.confidence,
        work_authorization_status=authorization_status,
        work_authorization_reason=authorization_reason,
        degree_status=degree_status,
        degree_reason=degree_reason,
    )
