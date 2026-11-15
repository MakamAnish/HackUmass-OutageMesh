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
    """Safety floor: AI may raise the 1-5 urgency derived from keywords."""
    keyword_priority = keyword_priority_1_to_5(keyword)
    ai_priority = 1
    if isinstance(ai, dict):
        urgency = ai.get("urgency")
        if isinstance(urgency, int) and not isinstance(urgency, bool):
            ai_priority = max(1, min(5, urgency))
        else:
            ai_priority = {"LOW": 1, "MEDIUM": 3, "HIGH": 4}.get(
                ai.get("level"), 1
            )
    final_priority = max(keyword_priority, ai_priority)
    if final_priority >= 4:
        return "HIGH"
    if final_priority >= 3:
        return "MEDIUM"
    return "LOW"


def keyword_priority_1_to_5(keyword: dict) -> int:
    """Map the existing 0-100 keyword score onto the AI urgency scale."""
    if isinstance(keyword, dict):
        score = keyword.get("score")
        if isinstance(score, int) and not isinstance(score, bool):
            return max(1, min(5, (score + 19) // 20))
        return {"LOW": 1, "MEDIUM": 3, "HIGH": 4}.get(
            keyword.get("level"), 1
        )
    return 1


def keyword_urgency(category, text):
    return keyword_score(category, text)["score"]


def final_urgency(category, text, ai_score=None):
    """Backward-compatible helper for older code that expects a numeric urgency."""
    keyword = keyword_score(category, text)
    floor = keyword["score"]
    if isinstance(ai_score, int):
        floor = max(floor, ai_score)
    return floor
