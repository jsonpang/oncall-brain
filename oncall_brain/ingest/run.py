"""Ingest orchestrator: deterministic + LLM + curated -> validate -> write candidates.

Usage (from repo root): python -m oncall_brain.ingest.run [--no-llm]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import db
from . import deterministic, evidence as EV, llm

ROOT = Path(__file__).resolve().parents[2]
REJECTED = ROOT / "ingest" / "rejected.jsonl"


def run_llm_extraction(verbose=False) -> tuple[list[dict], list[dict]]:
    """Run LLM slices over all chats; return (edges, rejects)."""
    edges, rejects = [], []
    from .labeler import label_of
    from .. import schema as S
    chat_dir = EV.ROOT / "corpus" / "chats"
    files = sorted(chat_dir.glob("chat_*.md"))
    n_calls = 0
    for f in files:
        src = f"corpus/chats/{f.name}"
        for chunk, offset in llm.slice_chat(src):
            out = llm.llm_extract_slice(chunk, src)
            n_calls += 1
            for a in out:
                line = a.get("evidence_line")
                edge = {
                    "subject": a.get("subject"), "predicate": a.get("predicate"),
                    "object": a.get("object"), "source": src,
                    "line": offset + int(line) if line else 0,
                    "authority": a.get("authority", "observation"),
                    "valid_from": a.get("valid_from") or "2026-01-01",
                    "valid_to": a.get("valid_to"),
                    "state": "candidate",
                    "props": {k: v for k, v in (a.get("props") or {}).items() if v},
                }
                # validate: predicate, domain/range, line exists, quote on line
                reason = None
                if not S.is_registered(edge["predicate"]):
                    reason = f"unregistered predicate '{edge['predicate']}'"
                else:
                    sl, ol = label_of(str(edge["subject"])), label_of(str(edge["object"]))
                    reason = S.validate_predicate(edge["predicate"], sl, ol)
                    if reason is None:
                        ls = EV.lines(src)
                        if not (1 <= edge["line"] <= len(ls)):
                            reason = f"line {edge['line']} out of range"
                        else:
                            q = (a.get("evidence_text") or "").strip()
                            if q and q[:40] not in ls[edge["line"] - 1]:
                                reason = "quoted evidence_text not found on line"
                if reason:
                    rejects.append({**edge, "origin": "llm", "reason": reason})
                else:
                    edges.append(edge)
        if verbose:
            print(f"  {src}: done")
    print(f"LLM: {n_calls} slice calls, {len(edges)} edges accepted, {len(rejects)} rejected")
    return edges, rejects


def build_payload(llm_edges: list[dict], llm_rejects: list[dict]):
    payload, rejects = [], []

    # 1. deterministic sources
    det_payload, det_rejects = deterministic.extract()
    payload.extend(det_payload)
    rejects.extend({**r, "origin": "deterministic"} for r in det_rejects)

    # 2. curated chat/doc assertions (validated)
    for a in llm.curated():
        reason = llm.validate(a)
        if reason:
            rejects.append({**a, "origin": "curated", "reason": reason})
            continue
        ev = EV.build(a["source"], a["line"], a["authority"])
        assert ev is not None, f"evidence missing {a['source']}:{a['line']}"
        aid = f"cur:{a['predicate']}:{a['subject']}->{a['object']}:{a['source']}:{a['line']}"
        payload.append({"op": "evidence", **ev})
        payload.append({"op": "edge", "subject": a["subject"], "predicate": a["predicate"],
                        "object": a["object"], "aid": aid, "authority": a["authority"],
                        "evidence": [ev["id"]], "state": a.get("state", "candidate"),
                        "valid_from": a["valid_from"], "valid_to": a["valid_to"],
                        "props": a.get("props", {})})

    # 3. extra nodes referenced by curated edges
    for label, nid, name in llm.EXTRA_NODES:
        payload.append({"op": "node", "label": label, "id": nid,
                        "props": {"name": name, "kind": label}})

    # 4. LLM-slice edges (already validated) -- skip those duplicating curated
    curated_keys = {(a["subject"], a["predicate"], a["object"]) for a in llm.curated()}
    for e in llm_edges:
        if (e["subject"], e["predicate"], e["object"]) in curated_keys:
            continue
        ev = EV.build(e["source"], e["line"], e["authority"])
        if ev is None:
            rejects.append({**e, "origin": "llm", "reason": "evidence build failed"})
            continue
        aid = f"llm:{e['predicate']}:{e['subject']}->{e['object']}:{e['source']}:{e['line']}"
        payload.append({"op": "evidence", **ev})
        payload.append({"op": "edge", "subject": e["subject"], "predicate": e["predicate"],
                        "object": e["object"], "aid": aid, "authority": e["authority"],
                        "evidence": [ev["id"]], "state": "candidate",
                        "valid_from": e["valid_from"], "valid_to": e["valid_to"],
                        "props": e.get("props", {})})
    rejects.extend({"origin": "llm", **r} for r in llm_rejects)
    return payload, rejects


def main(use_llm=True):
    db.wipe()
    db.ensure_indexes()

    llm_edges, llm_rejects = ([], [])
    if use_llm:
        llm_edges, llm_rejects = run_llm_extraction()

    payload, rejects = build_payload(llm_edges, llm_rejects)

    # ensure Person nodes exist for person IDs referenced by curated/LLM edges
    from .labeler import label_of, roster_names
    known = {i["id"] for i in payload if i["op"] == "node"}
    roster = roster_names()
    for item in payload:
        if item["op"] == "edge":
            for end in (item["subject"], item["object"]):
                if end not in known and end in roster:
                    payload.append({"op": "node", "label": "Person", "id": end,
                                    "props": {"name": end, "kind": "Person"}})
                    known.add(end)

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
