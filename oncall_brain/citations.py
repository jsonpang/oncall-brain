"""Citation joins use only the evidence returned for the current question."""
from __future__ import annotations

import hashlib
import re


def citation_pairs(results: list[dict]) -> set[tuple[str, str]]:
    supported = set()
    for result in results:
        sources = result.get("source_evidence", [])
        evidence = {e["id"]: e for e in result.get("evidence", []) + sources
                    if isinstance(e, dict) and "id" in e}
        joins = [(a["aid"], a.get("evidence", [])) for a in result.get("assertions", [])]
        for conflict in result.get("conflicts", []):
            joins.extend((v["aid"], v.get("evidence", [])) for v in conflict["values"])
        for path in result.get("paths", []):
            joins.extend(zip(path.get("aids", []), path.get("evidence_ids", [])))
        joins.extend((e.get("aid", e["id"]), [e["id"]]) for e in sources)
        for aid, ids in joins:
            for eid in ids:
                ev = evidence.get(eid)
                if ev and aid:
                    supported.add((aid, f"{ev['source']}:{ev['locator']}"))
    return supported


def compact_tool_result(result: dict) -> dict:
    """Short display aids retain graph identity without repeating locators."""
    def compact(aid):
        if isinstance(aid, str) and ("corpus/" in aid or "->" in aid):
            return "a" + hashlib.sha256(aid.encode()).hexdigest()[:16]
        return aid

    def visit(value):
        if isinstance(value, dict):
            return {k: compact(v) if k == "aid" else
                    [compact(a) for a in v] if k == "aids" else visit(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v) for v in value]
        return value

    out = visit(result)
    for source in out.get("source_evidence", []):
        source.setdefault("aid", compact(source["id"]))
    return out


def parse_citation(citation: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+)\s+(?:->|\u2192)\s+(corpus/[^:\]\n]+:\d+)", citation)
    return (match[1].strip(), match[2]) if match else None


def normalize_citations(answer: str, supported: set[tuple[str, str]]) -> str:
    def replace(match):
        raw = match[1]
        for aid, locator in sorted(supported):
            prefix = re.match(re.escape(aid) + r"\s+(?:->|\u2192)\s+", raw)
            if prefix:
                locators = re.split(r"\s+(?:->|\u2192)\s+", raw[prefix.end():])
                if locators and all(loc == locator for loc in locators):
                    return f"[{aid} \u2192 {locator}]"
        return match[0]
    return re.sub(r"\[([^\]\n]+)\]", replace, answer)
