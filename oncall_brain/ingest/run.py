"""Ingest orchestrator: deterministic + LLM -> validate -> canonicalise -> write.

Usage (from repo root): python -m oncall_brain.ingest.run [--no-llm]
"""
from __future__ import annotations

import json
import hashlib
import re
import sys
from pathlib import Path

from .. import db
from . import deterministic, evidence as EV, llm

ROOT = Path(__file__).resolve().parents[2]
REJECTED = ROOT / "ingest" / "rejected.jsonl"


def run_llm_extraction(verbose=False):
    """Run LLM slices over chats and free-text docs; return (edges, rejects)."""
    from .. import schema as S
    from .labeler import label_of

    vocab = llm.vocabulary()
    edges, rejects = [], []
    n_calls = 0
    for src in llm.sources():
        kind = "doc" if src.startswith("corpus/docs/") else "chat"
        for chunk, offset in llm.slice_source(src):
            out = llm.llm_extract_slice(chunk, src, kind=kind, vocab=vocab)
            n_calls += 1
            ls = EV.lines(src)
            for a in out:
                a = llm.repair_direction(a)
                line = a.get("evidence_line")
                # The slice prints absolute line numbers ([189] is line 189 of
                # the file), so the offset must NOT be added again -- doing so
                # pushed every second-and-later slice past EOF and rejected
                # those assertions as "line out of range".
                edge = {
                    "subject": str(a.get("subject") or "").strip(),
                    "predicate": a.get("predicate"),
                    "object": str(a.get("object") or "").strip(),
                    "source": src,
                    "line": int(line) if line else 0,
                    "authority": a.get("authority") or "observation",
                    "valid_from": a.get("valid_from") or "2026-01-01",
                    "valid_to": a.get("valid_to"),
                    "state": "candidate",
                    "props": {k: v for k, v in (a.get("props") or {}).items()
                              if v is not None},
                    # Drop the slice's "[28] " line number if the model quoted it.
                    "evidence_text": re.sub(r"^\s*\[\d+\]\s*", "",
                                            a.get("evidence_text") or ""),
                }
                reason = None
                if not edge["subject"] or not edge["object"]:
                    reason = "missing subject or object"
                elif not S.is_registered(edge["predicate"]):
                    reason = f"unregistered predicate '{edge['predicate']}'"
                else:
                    sl = label_of(edge["subject"])
                    ol = label_of(edge["object"])
                    reason = S.validate_predicate(edge["predicate"], sl, ol)
                    if reason is None and not (
                            1 <= edge["line"] <= len(ls)):
                        reason = f"line {edge['line']} out of range"
                    if reason is None and not llm.quote_matches(edge):
                        reason = "quoted evidence_text not found on line"
                if reason:
                    rejects.append({**edge, "origin": "llm", "reason": reason})
                else:
                    edges.append(edge)
        if verbose:
            print(f"  {src}: done")
    print(f"LLM: {n_calls} slices (including cache hits), {len(edges)} edges accepted, "
          f"{len(rejects)} rejected")
    return edges, rejects


def build_payload(llm_edges: list[dict], llm_rejects: list[dict]):
    payload, rejects = [], []

    # 1. deterministic sources (org, tickets, module registry)
    det_payload, det_rejects = deterministic.extract()
    payload.extend(det_payload)
    rejects.extend({**r, "origin": "deterministic"} for r in det_rejects)

    known = {i["id"] for i in payload if i["op"] == "node"}

    # 2. LLM-derived assertions, aligned onto canonical ids
    llm_edges, aliases = llm.canonicalise(llm_edges, known)

    seen: dict[str, dict] = {}
    for e in llm_edges:
        # Evidence line is not identity: identical claims in the same source
        # accumulate evidence. Authority, conditions and dates remain distinct.
        key = json.dumps({k: e.get(k) for k in (
            "subject", "predicate", "object", "source", "authority",
            "valid_from", "valid_to", "props")}, sort_keys=True, ensure_ascii=False)
        ev = EV.build(e["source"], e["line"], e["authority"],
                      e.get("evidence_text") or None)
        if ev is None:
            rejects.append({**e, "origin": "llm", "reason": "evidence build failed"})
            continue
        aid = "llm:" + hashlib.sha256(key.encode()).hexdigest()[:24]
        payload.append({"op": "evidence", **ev})
        if key in seen:
            if ev["id"] not in seen[key]["evidence"]:
                seen[key]["evidence"].append(ev["id"])
            continue
        assertion = {"op": "edge", "subject": e["subject"],
                        "predicate": e["predicate"], "object": e["object"],
                        "aid": aid, "authority": e["authority"],
                        "evidence": [ev["id"]], "state": "candidate",
                        "valid_from": e["valid_from"], "valid_to": e["valid_to"],
                        "props": e.get("props", {})}
        payload.append(assertion)
        seen[key] = assertion

    # Every LLM endpoint must be searchable by kind/name, not only roster people.
    from .labeler import label_of
    for e in llm_edges:
        for nid in (e["subject"], e["object"]):
            if nid not in known:
                kind = label_of(nid)
                name = llm._split_id(nid)[1].replace("_", " ")
                payload.append({"op": "node", "label": kind, "id": nid,
                                "props": {"kind": kind, "name": name}})
                known.add(nid)

    rejects.extend({"origin": "llm", **r} for r in llm_rejects)
    if aliases:
        (ROOT / "ingest" / "aliases.json").write_text(
            json.dumps(aliases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"entity alignment: {len(aliases)} id variants merged onto canonical ids")
    return payload, rejects


def main(use_llm=True):
    llm_edges, llm_rejects = ([], [])
    if use_llm:
        llm_edges, llm_rejects = run_llm_extraction()

    payload, rejects = build_payload(llm_edges, llm_rejects)
    if use_llm:
        from .claims import annotate
        annotate(payload)

    db.wipe()
    db.ensure_indexes()
    db.batch_write(payload)
    REJECTED.parent.mkdir(exist_ok=True)
    with open(REJECTED, "w", encoding="utf-8") as f:
        for r in rejects:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    st = db.stats()
    print(f"written: {st['nodes']} nodes, {st['edges']} edges, {st['evidence']} evidence")
    print(f"rejected: {len(rejects)} -> ingest/rejected.jsonl")
    return st


if __name__ == "__main__":
    main(use_llm="--no-llm" not in sys.argv)
