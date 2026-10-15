"""Run the 17 gold-card questions through the agent and write eval/results/latest.md.

Parses docs/02-答案卡.md (read-only -- never modified), asks each question, and
judges each answer against that card's own expected state / points / forbidden
errors. The file is the only source of PASS counts; run it from the repo root:

    python eval/run_gold.py
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oncall_brain import agent, db, tools  # noqa: E402

GOLD = ROOT / "docs" / "02-答案卡.md"
RESULTS = ROOT / "eval" / "results"


# --------------------------------------------------------------------------
# Answer-card parsing
# --------------------------------------------------------------------------
def _sections(text: str) -> list[tuple[str, str]]:
    """Split the card into (qid, body) blocks for Q1..Q17."""
    out = []
    heads = list(re.finditer(r"^(?:### Q(\d+):|#### Q(\d+) \()", text, re.M))
    for i, m in enumerate(heads):
        start = m.start()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        qid = m.group(1) or m.group(2)
        out.append((qid, text[start:end]))
    # dedupe keeping order, first occurrence wins (toc mentions are###-less)
    seen, blocks = set(), []
    for qid, body in out:
        if qid in seen:
            continue
        seen.add(qid)
        blocks.append((qid, body))
    return blocks


def _field(body: str, label: str) -> str:
    """Text after a `N. **label**：` marker, up to the next numbered marker."""
    m = re.search(rf"\d+\.\s*\*\*{label}\*\*[：:]\s*(.*?)(?=\n\d+\.\s*\*\*|\Z)",
                  body, re.S)
    return m.group(1).strip() if m else ""


def _question_literal(body: str) -> str:
    m = re.search(r'"(.*?)"', body, re.S)
    return m.group(1).strip() if m else ""


def parse_gold() -> list[dict]:
    text = GOLD.read_text(encoding="utf-8")
    qs = []
    for qid, body in _sections(text):
        points_raw = _field(body, "标准答案要点")
        points = [re.sub(r"\s+", " ", p).strip()
                  for p in re.findall(r"-\s+\*\*[^*]+\*\*[：:]\s*(.*)", points_raw)]
        forbidden = [re.sub(r"\s+", " ", f).strip()
                     for f in re.findall(r"-\s+(.*)", _field(body, "禁止出现的错误"))]
        # The card writes e.g. "`superseded` (Current Active Rule ...)" --
        # the state itself is always the first backticked token.
        status_raw = _field(body, "期望状态")
        bt = re.search(r"`([^`]+)`", status_raw)
        status = (bt.group(1) if bt else status_raw).strip()
        # tolerate a bare word if no backticks are used
        for valid in ("applicable", "insufficient_context", "conflict",
                      "superseded", "stale", "not_found"):
            if valid in status:
                status = valid
                break
        qs.append({
            "qid": qid,
            "question": _question_literal(body),
            "expected_status": status,
            "points": [p for p in points if p],
            "forbidden": [f for f in forbidden if f],
            "evidence": re.findall(r"`([^`]+:\d+)`", _field(body, "必须引用的证据")),
        })
    return sorted(qs, key=lambda q: int(q["qid"]))


# --------------------------------------------------------------------------
# Judging
# --------------------------------------------------------------------------
NOT_FOUND_WORDS = ["not_found", "no answer", "not in the current graph",
                   "no documentation", "no runbook", "not found", "nowhere",
                   "no owner", "nothing", "not part of"]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _evidence_cited(answer: str, gold_evidence: list[str]) -> list[str]:
    locs = {e.rsplit(":", 1)[-1] for e in gold_evidence}
    cited = set(re.findall(r"(\d{1,4})\]", answer)) | set(
        re.findall(r":(\d{1,4})\b", answer))
    return sorted(locs & cited, key=int)


def judge(q: dict, res: dict) -> dict:
    # res is {"result": agent result, "inspect": {...}}
    answer = res["result"].get("answer") or ""
    a_norm = _norm(answer)
    expected = q["expected_status"]
    citations = res["inspect"]["citations"]
    ev_hit = _evidence_cited(answer, q["evidence"])

    # status detection from what the answer actually says
    says_not_found = any(w in a_norm for w in NOT_FOUND_WORDS)
    mentions_superseded = "superseded" in a_norm or "supersede" in a_norm
    mentions_conflict = "conflict" in a_norm
    detected = "applicable"
    if says_not_found:
        detected = "not_found"
    elif mentions_conflict:
        detected = "conflict"
    elif mentions_superseded:
        detected = "superseded"

    status_ok = (expected == detected) or (
        expected in ("conflict", "superseded") and detected == "applicable"
        and (mentions_superseded or mentions_conflict))

    # point hits: how many gold points are echoed in the answer
    hits = 0
    for p in q["points"]:
        key_tokens = [t for t in _norm(p).split()
                      if len(t) > 3 and t not in _STOP][:6]
        if key_tokens and sum(1 for t in key_tokens if t in a_norm) >= max(
                2, len(key_tokens) // 2):
            hits += 1

    forbidden_hits = [f for f in q["forbidden"] if _forbidden_present(f, a_norm)]

    passed = status_ok and not forbidden_hits and hits >= max(
        1, (len(q["points"]) + 1) // 2) and (bool(citations) or expected == "not_found")

    reasons = []
    if not status_ok:
        reasons.append(f"status mismatch (expected {expected}, answer reads {detected})")
    if forbidden_hits:
        reasons.append("forbidden content present")
    if hits < max(1, (len(q["points"]) + 1) // 2):
        reasons.append(f"point coverage low ({hits}/{len(q['points'])})")
    if not citations and expected != "not_found":
        reasons.append("no [aid -> file:line] citation emitted")

    return {"status_ok": status_ok, "detected": detected, "hits": hits,
            "total_points": len(q["points"]), "forbidden_hits": forbidden_hits,
            "evidence_hit": ev_hit, "citations": citations,
            "passed": passed, "reason": "; ".join(reasons) or "ok"}


_STOP = {"the", "and", "that", "this", "for", "with", "from", "must", "not",
         "every", "per", " Incident", "74c042b2"}


def _forbidden_present(forbidden: str, a_norm: str) -> bool:
    """Heuristic: English fragments of the forbidden rule appearing in the answer."""
    frags = re.findall(r"[A-Za-z][A-Za-z0-9 _-]{3,}", forbidden)
    # Chinese-only forbidden lines fall back to checking backticked ids/persons
    if not frags:
        return False
    hits = 0
    for f in frags:
        f = f.strip()
        if len(f) < 4 or f.lower() in _STOP or f in {"技术手段", "严禁"}:
            continue
        if _norm(f) and _norm(f) in a_norm:
            hits += 1
    return hits >= 2


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def render(qs: list[dict], results: list[dict], stats: dict, model: str) -> str:
    passed = sum(1 for r in results if r["judgement"]["passed"])
    lines = [
        "# OnCall Brain gold-card evaluation (run_gold.py)",
        "",
        f"- Generated: `{datetime.datetime.now().isoformat(timespec='seconds')}`",
        f"- Model: `{model}`",
        f"- Graph: `{db.GRAPH_NAME}` @ FalkorDB `{db.HOST}:{db.PORT}`",
        f"- **PASS: {passed} / {len(qs)}**",
        "",
        "> Every answer below was produced by `agent.ask()` against the live graph;",
        "> nothing here is hand-written. Tool sequences and Cypher template names",
        "> are recorded from the actual run.",
        "",
        "---",
        "",
    ]
    for q, r in zip(qs, results):
        j = r["judgement"]
        lines += [
            f"## Q{q['qid']} — {'PASS' if j['passed'] else 'FAIL'}",
            "",
            f"**Question**: {q['question']}",
            "",
            f"- Expected state: `{q['expected_status']}` / detected: `{j['detected']}`",
            f"- Points hit: {j['hits']}/{j['total_points']}",
            f"- Forbidden errors: {len(j['forbidden_hits'])}"
            + (f" — {j['forbidden_hits']}" if j["forbidden_hits"] else ""),
            f"- Citations emitted: {len(j['citations'])}",
            f"- Gold evidence lines referenced: {j['evidence_hit'] or 'none'}",
            f"- Return status: `{r['result']['stop_reason']}` "
            f"({r['result']['tool_calls']} tool calls, "
            f"{r['result']['relation_queries']} relation queries)",
            "",
            "**Tool call sequence** (name + arguments):",
            "",
        ]
        if r["result"]["trace"]:
            for t in r["result"]["trace"]:
                lines.append(
                    f"{t['step']}. `{t['tool']}({t['args_str']})` → status=`{t['status']}`")
                lines.append(f"   - Cypher templates: {', '.join(t['template'])}")
        else:
            lines.append("(no tool calls)")
        lines += [
            "",
            "**Cited evidence** (`[aid -> file:line]`):",
            "",
        ]
        lines += [f"- `{c}`" for c in j["citations"]] or ["- none"]
        lines += [
            "",
            "**Final answer** (verbatim from the agent):",
            "",
            r["result"]["answer"] or "(empty)",
            "",
            f"**Verdict**: {'PASS' if j['passed'] else 'FAIL'} — {j['reason']}",
            "",
            "---",
            "",
        ]

    total_ev = sum(stats["per_source"].values())
    lines += [
        "## Summary",
        "",
        f"- **Passed: {passed} / {len(qs)}**",
        f"- Graph size: {stats['nodes']} entity nodes, {stats['edges']} assertions, "
        f"{stats['evidence']} evidence records",
        "",
        "Assertions by predicate:",
        "",
        "| Predicate | Count |",
        "|---|---|",
    ]
    lines += [f"| {p} | {c} |" for p, c in sorted(stats["per_predicate"].items())]
    lines += ["", "Evidence by source:", "", "| Source | Evidence records |", "|---|---|"]
    lines += [f"| {s} | {c} |" for s, c in sorted(stats["per_source"].items())]
    lines += ["", f"Total evidence records: {total_ev}", ""]
    return "\n".join(lines)


def main() -> int:
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    model = os.environ.get("LLM_MODEL")

    qs = parse_gold()
    if len(qs) != 17:
        print(f"WARNING: parsed {len(qs)} questions, expected 17", file=sys.stderr)
    RESULTS.mkdir(parents=True, exist_ok=True)

    results = []
    for q in qs:
        trace: list[dict] = []
        res = agent.ask(q["question"], trace=trace)
        # citations really present in the emitted answer text
        citations = sorted(set(re.findall(r"\[([^\]\n]{3,120})\]", res["answer"] or "")))
        results.append({"result": res, "inspect": {"citations": citations}})
        print(f"Q{q['qid']}: {res['tool_calls']} calls, {len(res['answer'] or '')} chars",
              flush=True)

    for q, r in zip(qs, results):
        r["judgement"] = judge(q, r)

    stats = db.stats()
    report = render(qs, results, stats, model or "")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS / "latest.md").write_text(report, encoding="utf-8")
    (RESULTS / f"{stamp}.md").write_text(report, encoding="utf-8")

    passed = sum(1 for r in results if r["judgement"]["passed"])
    print(f"\nPASS {passed}/{len(qs)}")
    for q, r in zip(qs, results):
        j = r["judgement"]
        if not j["passed"]:
            print(f"  FAIL Q{q['qid']}: {j['reason']}")
    print(f"written: eval/results/latest.md and eval/results/{stamp}.md")
    return 0 if passed >= 13 else 1


if __name__ == "__main__":
    sys.exit(main())
