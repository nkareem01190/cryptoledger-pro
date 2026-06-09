"""
quality_log.py — Centralised data quality issue collector.
All modules append issues here; the Excel report reads this at the end.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class QualityIssue:
    category: str          # e.g. "No USD Price", "FIFO Gap", "Duplicate Removed"
    detail:   str          # human-readable description
    severity: str = "Warning"   # "Info" | "Warning" | "Error"
    tx_hash:  Optional[str] = None
    token:    Optional[str] = None
    chain:    Optional[str] = None


_issues: list[QualityIssue] = []


def log(category: str, detail: str, severity: str = "Warning",
        tx_hash: str = None, token: str = None, chain: str = None):
    _issues.append(QualityIssue(
        category=category, detail=detail, severity=severity,
        tx_hash=tx_hash, token=token, chain=chain,
    ))


def all_issues() -> list[QualityIssue]:
    return list(_issues)


def clear():
    _issues.clear()


def count_by_category() -> dict:
    counts = {}
    for issue in _issues:
        counts[issue.category] = counts.get(issue.category, 0) + 1
    return counts


def summary_lines() -> list[str]:
    counts = count_by_category()
    return [f"{cat}: {n}" for cat, n in sorted(counts.items())]
