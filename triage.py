import re

CATEGORIES = ("MED", "TRAP", "WATER", "SAFE", "OTHER", "SOS")

HIGH_WORDS = {
    "insulin",
    "bleeding",
    "unconscious",
    "not breathing",
    "can't breathe",
    "cant breathe",
    "chest pain",
    "seizure",
    "overdose",
    "fire",
    "trapped",
    "stuck",
    "collapsed",
    "pregnant",
    "labor",
}
MED_WORDS = {
    "injured",
    "hurt",
    "water",
    "cold",
    "elderly",
    "wheelchair",
    "oxygen",
}


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", haystack) is not None


def keyword_score(category: str, text: str) -> dict:
    """Return keyword-level triage data with reasons and a 0-100 score."""
    category = (category or "OTHER").upper()
    cleaned = _normalize(text)
    reasons = []
    score = 0

    if category == "SOS":
        return {"level": "HIGH", "score": 100, "reasons": ["sos"]}

    if category in {"MED", "TRAP"}:
        reasons.append(f"cat:{category}")
        score = max(score, 80)
    elif category == "WATER":
        reasons.append("cat:WATER")
        score = max(score, 55)
    elif category == "SAFE":
        reasons.append("cat:SAFE")
        score = max(score, 20)

    for word in sorted(HIGH_WORDS, key=len, reverse=True):
        if _contains_phrase(cleaned, word):
            reasons.append(word)
            score = max(score, 100)
            break

    if score < 100:
        for word in sorted(MED_WORDS, key=len, reverse=True):
            if _contains_phrase(cleaned, word):
                reasons.append(word)
                score = max(score, 60)
                break

    if not reasons:
        level = "LOW"
        score = 20
    elif score >= 80:
        level = "HIGH"
    elif score >= 55:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {"level": level, "score": score, "reasons": reasons}


def final_level(keyword: dict, ai: dict | None) -> str:
    """Safety floor: AI may raise urgency but never lower it."""
    levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    keyword_level = keyword.get("level", "LOW") if isinstance(keyword, dict) else "LOW"
    keyword_rank = levels.get(keyword_level, 1)
    ai_rank = 1
    if isinstance(ai, dict) and ai.get("level") in levels:
        ai_rank = levels[ai["level"]]
    return "HIGH" if max(keyword_rank, ai_rank) >= levels["HIGH"] else "MEDIUM" if max(keyword_rank, ai_rank) >= levels["MEDIUM"] else "LOW"


def keyword_urgency(category, text):
    return keyword_score(category, text)["score"]


def final_urgency(category, text, ai_score=None):
    """Backward-compatible helper for older code that expects a numeric urgency."""
    keyword = keyword_score(category, text)
    floor = keyword["score"]
    if isinstance(ai_score, int):
        floor = max(floor, ai_score)
    return floor
