"""
Keyword-based complaint classifier.
Isolated so an ML model (TF-IDF + Naive Bayes) can replace classify() later
without modifying any other module.
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


def classify(text: str) -> tuple[str, str]:
    """
    Classify complaint text into (department, priority).

    Department: first matching DEPARTMENT_KEYWORDS entry; defaults to 'General'.
    Priority: 'High' if any HIGH_KEYWORDS match, 'Medium' if any MEDIUM_KEYWORDS
    match, otherwise 'Low'.
    """
    if not isinstance(text, str):
        return "General", "Low"

    lower = text.lower()

    department = "General"
    for dept, keywords in DEPARTMENT_KEYWORDS.items():
        if _matches_any(lower, keywords):
            department = dept
            break

    if _matches_any(lower, HIGH_KEYWORDS):
        priority = "High"
    elif _matches_any(lower, MEDIUM_KEYWORDS):
        priority = "Medium"
    else:
        priority = "Low"

    return department, priority
