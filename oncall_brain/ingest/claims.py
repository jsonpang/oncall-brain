"""Evidence-grounded semantic slots; no rubric or question is an input.

The LLM only normalises claims. The read-only tools compare their values and
conditions deterministically, retaining every source and authority level.
"""
from __future__ import annotations

import hashlib
import json

from . import llm
from ..json_response import object_from_response

VERSION = "semantic-slots-v3-batched"
PROMPT = """Normalise explicit operational claims from the evidence records below.
Return JSON {"claims": [{"evidence_id": "exact supplied ID", "subject": "exact
entity ID from the supplied catalog", "predicate": "short semantic slot name",
"value": "canonical value", "condition": {"dimension": "value"},
"quote": "verbatim supporting substring from that evidence record"}]}.

Use the SAME subject and predicate for statements that answer the SAME business
question, regardless of source, authority, agreement, or wording. Use stable
canonical values and units, separating distinct stages, duties, and measurements
into distinct predicates. Conditions are equality constraints; use {} when none.
The subject is the thing whose property is discussed, not the speaker or source.
Use existing catalog IDs, preferring the shared module/resource/procedure to
the ID of a Rule encoding a particular value. Do not turn two consecutive stages
into disagreements; distinguish acknowledgement, diagnosis, and resolution.
Document curation, technical troubleshooting, resource configuration and routing
are distinct duties. NEVER infer troubleshooting from curation. A chat statement
explicitly assigning someone a duty is still a claim, even when mistaken. A
question, correction, negated claim or historical/replaced practice is NOT an
affirmed current value. Extract corrections as their positive corrected claim.
Only emit values actually supported by the quoted evidence. No world knowledge.
For a table use its header to interpret columns. Do not invent missing facts.
Do not evaluate conflicts; only normalise the independent statements.
"""


def annotate(payload: list[dict]) -> None:
    """Attach normalised claims to their existing evidence-bearing assertions."""
    evidence = {p["id"]: p for p in payload if p["op"] == "evidence"
                and p["source"].startswith(("corpus/docs/", "corpus/chats/"))}
    edges = [p for p in payload if p["op"] == "edge"]
    used = {eid for e in edges for eid in e["evidence"] if eid in evidence}
    catalog = {p["id"]: p["props"].get("name", p["id"])
               for p in payload if p["op"] == "node" and p.get("label")
               not in ("Person", "Vendor", "Ticket", "Rule", "Notice", "Role")}
    records = []
    from . import evidence as EV
    for eid in sorted(used):
        ev = evidence[eid]
        lines = EV.lines(ev["source"])
        # Headers disambiguate table fields; short context also resolves pronouns.
        n = int(ev["locator"])
        context = "\n".join(lines[max(0, n - 4): n + 2])
        records.append({"id": eid, "text": ev["excerpt"], "context": context})
    cache = llm.CACHE_DIR / "slots"
    cache.mkdir(exist_ok=True, parents=True)
    data = {"claims": []}
    slots = []
    for start in range(0, len(records), 12):
        source = json.dumps({"catalog": catalog, "known_slots": slots,
            "evidence": records[start:start + 12]}, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256((VERSION + PROMPT + source).encode()).hexdigest()
        path = cache / f"{key}.json"
        if path.exists():
            batch = json.loads(path.read_text(encoding="utf-8"))
        else:
            response = llm._llm().chat.completions.create(
                model=llm._model(), messages=[{"role": "user", "content": PROMPT + "\n" + source}],
                temperature=0.0, reasoning_effort="low",
                response_format={"type": "json_object"}, max_tokens=12000)
            choice = response.choices[0]
            content = choice.message.content or ""
            if not content or getattr(choice, "finish_reason", "stop") != "stop":
                raise ValueError(f"incomplete slot JSON: chars={len(content)}, finish={getattr(choice, 'finish_reason', None)}")
            try:
                batch = object_from_response(content)
            except ValueError:
                raise ValueError(f"invalid slot JSON: chars={len(content)}, fenced={content.lstrip().startswith('```')}") from None
            if not isinstance(batch.get("claims"), list):
                raise ValueError("semantic slot extraction did not return claims")
            path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
        data["claims"].extend(batch["claims"])
        slots = sorted({(c["subject"], c["predicate"]) for c in data["claims"]
                        if isinstance(c, dict) and "subject" in c and "predicate" in c})
        print(f"semantic slot progress: {min(start + 12, len(records))}/{len(records)} records", flush=True)
    by_ev = {}
    accepted = rejected = 0
    for c in data.get("claims", []):
        eid = c.get("evidence_id")
        ev = evidence.get(eid)
        quote = c.get("quote")
        cond = c.get("condition")
        if (not ev or c.get("subject") not in catalog
                or not all(isinstance(c.get(k), str) and c[k].strip()
                           for k in ("predicate", "value", "quote"))
                or not isinstance(cond, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) for k, v in cond.items())
                or quote not in ev["excerpt"]):
            rejected += 1
            continue
        by_ev.setdefault(eid, []).append({k: c[k] for k in
                                         ("subject", "predicate", "value", "condition", "quote")})
        accepted += 1
    for edge in edges:
        claims = [dict(c, evidence_id=eid) for eid in edge["evidence"] for c in by_ev.get(eid, [])]
        if claims:
            edge.setdefault("props", {})["claims_json"] = json.dumps(claims, ensure_ascii=False)
            edge["props"]["claim_subjects"] = sorted({c["subject"] for c in claims})
    print(f"semantic slots: {len(records)} evidence records, {accepted} accepted, {rejected} rejected")
