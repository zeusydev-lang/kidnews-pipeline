"""Content checks shared by the agent and the admin page (build plan task 13).

- reading_level: Flesch-Kincaid grade, computed in code (target grades 6-8).
- attribution_problems: wording that credits AI-written text to NASA,
  which NASA's guidelines forbid.
"""

import re

# Wording the site must never use for AI-written text.
ATTRIBUTION_PATTERNS = [
    r"according to nasa",
    r"nasa says",
    r"nasa said",
    r"nasa reports",
    r"nasa reported",
    r"nasa scientists say",
    r"nasa explains",
    r"nasa confirms",
    r"nasa wrote",
    r"written by nasa",
    r"(reviewed|approved|endorsed|verified) by nasa",
]
_ATTRIBUTION = [re.compile(rf"\b{p}\b", re.IGNORECASE) for p in ATTRIBUTION_PATTERNS]

READING_LEVEL_TARGET = (6.0, 8.0)  # grades 6-8
READING_LEVEL_ALLOWED = (5.0, 9.5)  # outside this, the story needs another try


def attribution_problems(*texts: str) -> list[str]:
    """Return each forbidden phrase found in the texts (empty list = fine)."""
    combined = " ".join(t for t in texts if t)
    found = []
    for pattern, regex in zip(ATTRIBUTION_PATTERNS, _ATTRIBUTION, strict=True):
        if regex.search(combined):
            found.append(pattern)
    return found


def count_syllables(word: str) -> int:
    """Rough English syllable count (good enough for a grade estimate)."""
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", word)  # silent endings
    word = re.sub(r"^y", "", word)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", word)))


def reading_level(text: str) -> float:
    """Flesch-Kincaid grade level, rounded to one decimal, kept between 0 and 20
    (scores beyond that mean "far too hard" and aren't meaningful).
    0.0 for empty text."""
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]
    words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", text)
    if not sentences or not words:
        return 0.0
    syllables = sum(count_syllables(w) for w in words)
    grade = 0.39 * len(words) / len(sentences) + 11.8 * syllables / len(words) - 15.59
    return round(min(max(grade, 0.0), 20.0), 1)
