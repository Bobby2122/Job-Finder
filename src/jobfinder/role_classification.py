from __future__ import annotations

from dataclasses import dataclass

from .models import Job


AI_ML_MODELING_SIGNALS = (
    "machine learning",
    "ml model",
    "model training",
    "model evaluation",
    "artificial intelligence",
    " ai ",
    "llm",
    "large language model",
    "agent",
    "rag",
    "data science",
    "optimization",
    "simulation",
    "numerical methods",
    "robotics",
    "autonomy",
    "autonomous",
    "computer vision",
    "nlp",
    "forecasting",
    "statistical modeling",
    "statistical inference",
    "algorithms",
    "decision science",
    "scientific computing",
    "experimentation",
)

STRONG_SIGNALS = (
    "machine learning",
    "model training",
    "model evaluation",
    "llm",
    "large language model",
    "optimization",
    "simulation",
    "robotics",
    "autonomy",
    "autonomous",
    "computer vision",
    "statistical modeling",
    "scientific computing",
)

BUILD_RESPONSIBILITIES = (
    "build",
    "develop",
    "train",
    "evaluate",
    "deploy",
    "implement",
    "design",
    "prototype",
    "experiment",
    "productionize",
)

PURE_SWE_SCOPE_SIGNALS = (
    "frontend",
    "front-end",
    "web ui",
    "mobile",
    "ios",
    "android",
    "crud",
    "backend service",
    "backend services",
    "devops",
    "sre",
    "site reliability",
    "it support",
    "infrastructure operations",
    "on-call",
)


@dataclass(frozen=True)
class RoleClassification:
    classification: str
    reason: str
    confidence: str
    matched_signals: tuple[str, ...] = ()


def _matches(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term for term in terms if term in text))


def classify_role(job: Job) -> RoleClassification:
    title = job.title.lower()
    text = job.text
    matched = _matches(text, AI_ML_MODELING_SIGNALS)
    strong = _matches(text, STRONG_SIGNALS)
    responsibilities = _matches(text, BUILD_RESPONSIBILITIES)
    swe_scope = _matches(text, PURE_SWE_SCOPE_SIGNALS)
    software_title = any(
        term in title
        for term in (
            "software engineer",
            "backend engineer",
            "frontend engineer",
            "mobile engineer",
            "ios engineer",
            "android engineer",
            "forward deployed engineer",
        )
    )

    if "data engineer" in title and any(
        signal in text
        for signal in ("machine learning", "ml platform", "ml pipeline", "feature store")
    ):
        return RoleClassification(
            "data_engineering_for_ml",
            "Data engineering role supports ML platforms, pipelines, or features",
            "High",
            matched,
        )
    if any(signal in text for signal in ("optimization", "simulation", "forecasting", "numerical methods")):
        return RoleClassification(
            "optimization_modeling",
            "Posting includes optimization, simulation, forecasting, or numerical modeling scope",
            "High" if strong else "Medium",
            matched,
        )
    if any(signal in text for signal in ("machine learning", "llm", "artificial intelligence", " ai ", "rag", "computer vision", "nlp")):
        if len(matched) >= 2 or (strong and responsibilities):
            return RoleClassification(
                "ai_ml_engineering",
                "Software or analytical work has AI/ML signals plus build, train, evaluate, or deploy scope",
                "High",
                matched,
            )
        return RoleClassification(
            "mixed",
            "Contains AI/ML language but the core responsibility needs verification",
            "Medium",
            matched,
        )
    if any(signal in text for signal in ("robotics", "autonomy", "autonomous")):
        return RoleClassification(
            "ai_ml_engineering",
            "Robotics or autonomous-systems scope should remain eligible for ranking",
            "Medium",
            matched,
        )
    if software_title and swe_scope and not matched:
        return RoleClassification(
            "pure_swe",
            "Software role appears focused on frontend, mobile, backend CRUD, DevOps/SRE, support, or infrastructure operations without AI/ML/modeling evidence",
            "High",
            swe_scope,
        )
    if software_title and not matched:
        return RoleClassification(
            "uncertain",
            "Software role lacks enough AI/ML/optimization/modeling evidence; manual scope check recommended",
            "Low",
        )
    if matched:
        return RoleClassification(
            "mixed",
            "Role has at least one target technical signal but may be broader than AI/ML/modeling",
            "Medium",
            matched,
        )
    return RoleClassification(
        "uncertain",
        "No clear target role-classification evidence in title or description",
        "Low",
    )
