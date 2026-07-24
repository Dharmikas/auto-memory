"""Heuristics to detect likely recall-derived context.

Read-only mitigation: detect content that appears to be generated from
previous recall output and deprioritize it in ranking.
"""

from __future__ import annotations

import re


_RECALL_MARKERS = (
    r"\bsession-recall\b",
    r"\bsession recall\b",
    r"\bprogressive session recall\b",
    r"\bsession[-\s]?store\.db\b",
    r"\bsession[-\s]?state\b",
    r"\brecall(?:-derived)?\b",
    r"\bfrom (?:prior|previous|earlier) sessions\b",
    r"\bpick up where i left off\b",
)

_RECALL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _RECALL_MARKERS]


def recall_derivation_score(texts: list[str | None]) -> int:
    """Return a non-negative score; higher means more likely recall-derived."""
    score = 0
    for raw in texts:
        if not raw:
            continue
        text = str(raw)
        for pattern in _RECALL_PATTERNS:
            if pattern.search(text):
                score += 1
    return score


def recall_row_score(row: dict) -> int:
    """Best-effort recall-derived score from an output row/session dict."""
    if bool(row.get("_recall_derived")):
        return 100
    candidates = [
        row.get("summary"),
        row.get("excerpt"),
        row.get("session_summary"),
    ]
    return recall_derivation_score(candidates)
