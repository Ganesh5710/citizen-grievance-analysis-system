"""
Keyword-based complaint priority classifier (and optional department helper).
Isolated so an ML model can replace it later without touching any other module.
"""

import re

HIGH_KEYWORDS: list[str] = [
    "urgent", "critical", "emergency", "danger", "fire",
    "death", "blast", "accident", "hospital", "sparking",
    "explosion", "collapsed", "flood", "electrocution", "injury",
]

MEDIUM_KEYWORDS: list[str] = [
    "problem", "issue", "damaged", "delay", "overflow",
    "blocked", "leakage", "traffic", "garbage", "pothole",
    "dirty", "pollution", "crack", "repair",
]

DEPARTMENT_KEYWORDS: dict[str, list[str]] = {
    "Water":       ["water", "pipeline", "tap", "water supply", "no water"],
    "Roads":       ["road", "roads", "pothole", "potholes", "damaged road"],
    "Electricity": ["electricity", "power", "transformer", "no electricity", "sparking", "wire"],
    "Sanitation":  ["drainage", "garbage", "waste", "sewage", "smell"],
    "Traffic":     ["traffic", "signal", "accident", "junction"],
}


def _matches_any(lower_text: str, keywords: list[str]) -> bool:
    for kw in keywords:
        if " " in kw:
            if kw in lower_text:
                return True
        else:
            if re.search(r"\b" + re.escape(kw) + r"\b", lower_text):
                return True
    return False


def classify_priority(text: str) -> str:
    """Auto-detect complaint priority ('High', 'Medium', 'Low') from complaint text."""
    if not isinstance(text, str):
        return "Low"

    lower = text.lower()
    if _matches_any(lower, HIGH_KEYWORDS):
        return "High"
    if _matches_any(lower, MEDIUM_KEYWORDS):
        return "Medium"
    return "Low"


def classify(text: str) -> tuple[str, str]:
    """
    Return (department, priority).
    Department detection is preserved for standalone callers/tests; citizen submission
    uses citizen-selected department_id and calls classify() for priority detection.
    """
    if not isinstance(text, str):
        return "General / Other", "Low"

    lower = text.lower()
    department = "General / Other"
    for dept, keywords in DEPARTMENT_KEYWORDS.items():
        if _matches_any(lower, keywords):
            department = dept
            break

    return department, classify_priority(text)
