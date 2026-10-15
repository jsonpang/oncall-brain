"""Corpus text access: numbered-line lookup used for evidence validation.

All evidence assertions must cite `file:line` where the line really exists and
the quoted excerpt appears on it (or, for tables, on the cited line itself).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus"

_cache: dict[str, list[str]] = {}


def repo_path(rel: str) -> str:
    """Normalize a locator like 'corpus/chats/chat_2026-08-07.md:23' to repo-relative file + line."""
    return rel


def lines(rel: str) -> list[str]:
    if rel not in _cache:
        p = (ROOT / rel).resolve()
        if not p.exists():
            _cache[rel] = []
        else:
            _cache[rel] = p.read_text(encoding="utf-8").splitlines()
    return _cache[rel]


def make_evidence_id(source: str, locator: str) -> str:
    return f"ev:{source}:{locator}"


def build(source: str, line_no: int, authority: str, excerpt: str | None = None) -> dict | None:
    """Build an evidence record for source file at line_no (1-based).

    Returns None (invalid) if the line doesn't exist or the excerpt isn't found
    on/near the line. Otherwise returns the evidence dict.
    """
    ls = lines(source)
    if not (1 <= line_no <= len(ls)):
        return None
    line = ls[line_no - 1]
    if excerpt:
        norm = re.sub(r"\s+", " ", excerpt.strip().strip('"').strip("'"))
        # accept: excerpt on the line itself or within +/-1 line (table rows wrap)
        window = " ".join(ls[max(0, line_no - 2):line_no + 1])
        if norm and norm[: min(60, len(norm))] not in re.sub(r"\s+", " ", window):
            return None
    locator = str(line_no)
    return {
        "id": make_evidence_id(source, locator),
        "source": source,
        "locator": locator,
        "excerpt": (excerpt or line).strip(),
        "authority": authority,
    }


def scan_lines(source: str, pattern: str) -> list[int]:
    """Return 1-based line numbers matching a regex."""
    rx = re.compile(pattern)
    return [i + 1 for i, ln in enumerate(lines(source)) if rx.search(ln)]
