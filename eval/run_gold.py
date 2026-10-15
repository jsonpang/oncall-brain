"""Run the 17 gold-card questions through the agent and write eval/results/latest.md.

Parses docs/02-答案卡.md (read-only -- never modified), asks each question, and
judges each answer against that card's own expected state / points / forbidden
errors. The file is the only source of PASS counts; run it from the repo root:

    python eval/run_gold.py
"""
from __future__ import annotations

import datetime
from contextvars import ContextVar
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oncall_brain import agent, db  # noqa: E402
from oncall_brain.json_response import object_from_response  # noqa: E402

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
ANSWER_STATES = ["applicable", "insufficient_context", "conflict", "superseded", "not_found"]
JUDGE_SCHEMA_VERSION = "strict-v2"
JUDGE_PROMPT_VERSION = "2026-10-15-v2"
_pending_cache = ContextVar("pending_judge_cache", default=None)


class JudgeError(ValueError):
    """Provider or schema failure: invalid judgement, never a verdict."""


def _llm_detect_status(question: str, answer: str) -> str:
    first = answer.splitlines()[0] if answer else ""
    if first.startswith("Status: ") and first[8:] in ANSWER_STATES:
        return first[8:]
    raise JudgeError("missing or invalid first-line Status")


def _judge_json(prompt: str) -> dict:
    import os
    from dotenv import load_dotenv
    import openai

    load_dotenv(ROOT / ".env")
    try:
        client = openai.OpenAI(base_url=os.environ["LLM_BASE_URL"],
                               api_key=os.environ["LLM_API_KEY"])
        response = client.chat.completions.create(
            model=os.environ.get("LLM_JUDGE_MODEL") or os.environ["LLM_MODEL"],
            messages=[{"role": "user", "content": prompt}], temperature=0.0,
            response_format={"type": "json_object"})
        data = object_from_response(response.choices[0].message.content or "{}")
        if not isinstance(data, dict):
            raise JudgeError("judge response must be a JSON object")
        return data
    except JudgeError:
        raise
    except Exception as exc:
        # Do not retain provider messages, which may contain connection secrets.
        raise JudgeError(f"judge provider/JSON failure: {type(exc).__name__}") from None


def _boolean(data: dict, field: str) -> bool:
    if type(data.get(field)) is not bool:
        raise JudgeError(f"{field} must be a JSON boolean")
    return data[field]


def _evidence_cited(answer: str, gold_evidence: list[str]) -> list[str]:
    cited = set(re.findall(r"corpus/[^\s\]`]+:\d+", answer))
    return sorted(set(gold_evidence) & cited)


_POINT_JUDGE = """You are grading whether an answer covers required points.

QUESTION: {question}

ANSWER:
{answer}

For each numbered REQUIRED POINT below, decide whether the answer conveys it in
substance. Paraphrases count. The key detail (name, number, deadline, path, role
distinction) must actually be present -- a vague gesture does not count.

REQUIRED POINTS:
{points}

Reply with JSON only:
{{"results": [{{"index": 1, "covered": true/false, "why": "<few words>"}}, ...]}}
"""


def _llm_point_hits(question: str, answer: str, points: list[str]) -> list[bool]:
    """Ask the judge model whether each gold point is substantively covered."""
    if not points or not answer.strip():
        return [False] * len(points)
    payload = question + "||" + answer + "||" + "\n".join(points)
    return _cached("points", payload,
                   lambda: _llm_point_hits_uncached(question, answer, points))


def _llm_point_hits_uncached(question: str, answer: str,
                             points: list[str]) -> list[bool]:
    enumerated = "\n".join(f"{i}. {p}" for i, p in enumerate(points, 1))
    data = _judge_json(_POINT_JUDGE.format(question=question, answer=answer,
                                          points=enumerated))
    rows = data.get("results")
    if not isinstance(rows, list) or len(rows) != len(points):
        raise JudgeError("point indices must be complete and unique")
    flags = {}
    for row in rows:
        if not isinstance(row, dict):
            raise JudgeError("point result must be an object")
        index = row.get("index")
        if type(index) is not int or index not in range(1, len(points) + 1) or index in flags:
            raise JudgeError("invalid or duplicate point index")
        flags[index] = _boolean(row, "covered")
    return [flags[i] for i in range(1, len(points) + 1)]


def _count_point_hits(q: dict, answer: str) -> tuple[int, list[bool]]:
    flags = _llm_point_hits(q["question"], answer, q["points"])
    return sum(1 for f in flags if f), flags


_CARRIES_JUDGE = """You are checking whether an answer carries a specific kind of content.

QUESTION: {question}

ANSWER:
{answer}

Does the answer SUBSTANTIVELY carry {claim}?

Careful:
- A markdown heading alone is not content ("## Conflicts / Superseded" counts
  for nothing).
- "No conflict" / "nothing superseded" does NOT carry it.
- The content must actually be stated: for supersession, the old rule AND the
  replacement AND when the change took effect; for conflict, the two competing
  positions with their sources/authority.

Reply with JSON only: {{"carries": true/false}}
"""

_CARRIES_CLAIMS = {
    "superseded": "a supersession chain: the rule/practice being asked about was "
                  "replaced, naming the old one, the current replacement and the "
                  "effective date",
    "conflict": "a conflict: two or more competing answers to the same question, "
                "laid out with their sources or authority levels rather than "
                "silently resolved",
}


def _llm_carries(kind: str, question: str, answer: str) -> bool:
    """Does the answer substantively carry a supersession chain / a conflict?"""
    if not answer.strip():
        return False
    payload = f"{kind}||{question}||{answer}"
    return _cached("carries", payload,
                   lambda: _llm_carries_uncached(kind, question, answer))


def _llm_carries_uncached(kind: str, question: str, answer: str) -> bool:
    data = _judge_json(_CARRIES_JUDGE.format(
        question=question, answer=answer, claim=_CARRIES_CLAIMS[kind]))
    return _boolean(data, "carries")


def _supersession_evidence(question: str, answer: str) -> bool:
    return _llm_carries("superseded", question, answer)


def _conflict_evidence(question: str, answer: str) -> bool:
    return _llm_carries("conflict", question, answer)


def _verified_citations(res: dict) -> tuple[list[str], list[str]]:
    """Accept only exact aid/source/locator joins from this question's tools."""
    from oncall_brain.citations import citation_pairs, parse_citation
    supported = citation_pairs(res.get("tool_results", []))
    valid, invalid = [], []
    for citation in re.findall(r"\[([^\]\n]+)\]", res.get("answer", "")):
        pair = parse_citation(citation)
        if pair is not None or "corpus/" in citation or "->" in citation or "\u2192" in citation:
            (valid if pair in supported else invalid).append(citation)
    return sorted(set(valid)), sorted(set(invalid))


def judge(q: dict, res: dict) -> dict:
    pending = []
    token = _pending_cache.set(pending)
    try:
        verdict = _judge_valid(q, res)
        for path, value in pending:
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return verdict
    except JudgeError as exc:
        return {"valid": False, "detected": "judge_error", "passed": False,
                "reason": f"judge_error: {exc}", "status_ok": False,
                "hits": 0, "total_points": len(q["points"]), "point_detail": [],
                "forbidden_hits": [], "carries_superseded": False,
                "carries_conflict": False, "fabrication": False, "gave_up": False,
                "evidence_hit": [], "citations": []}
    finally:
        _pending_cache.reset(token)


def _judge_valid(q: dict, res: dict) -> dict:
    # res is {"result": agent result, "inspect": {...}}
    answer = res["result"].get("answer") or ""
    expected = q["expected_status"]
    citations, invalid_citations = _verified_citations(res["result"])
    ev_hit = _evidence_cited(answer, q["evidence"])

    detected = _llm_detect_status(q["question"], answer)
    carries_superseded = (_supersession_evidence(q["question"], answer)
                          if expected == "superseded" else False)
    carries_conflict = (_conflict_evidence(q["question"], answer)
                       if expected == "conflict" else False)
    status_ok = expected == detected
    if expected == "superseded":
        status_ok = status_ok and carries_superseded
    if expected == "conflict":
        status_ok = status_ok and carries_conflict

    # Hard behaviour 1: never fabricate. Where the card says the material has
    # no answer, the answer must actually declare that.
    fabrication = expected == "not_found" and detected != "not_found"
    # Hard behaviour 2: never give up. Where the card says the material does
    # answer, an answer that reports not_found is wrong however it got there.
    gave_up = expected != "not_found" and detected == "not_found"

    # point hits: is each gold point substantively present in the answer?
    hits, point_detail = _count_point_hits(q, answer)

    forbidden_hits = [f for f in q["forbidden"]
                      if _forbidden_present(f, answer, q["question"])]

    passed = (status_ok and not invalid_citations and not forbidden_hits and not fabrication
              and not gave_up and hits >= max(1, (len(q["points"]) + 1) // 2)
              and (bool(citations) or expected == "not_found"))

    reasons = []
    if invalid_citations:
        reasons.append("citations not supported by actual tool evidence")
    if fabrication:
        reasons.append("fabricated an answer where the card says not_found")
    if gave_up:
        reasons.append("reported not_found where the material does answer")
    if not status_ok and not (fabrication or gave_up):
        reasons.append(f"status mismatch (expected {expected}, answer reads {detected})")
    if forbidden_hits:
        reasons.append("forbidden content present")
    if hits < max(1, (len(q["points"]) + 1) // 2):
        reasons.append(f"point coverage low ({hits}/{len(q['points'])})")
    if not citations and expected != "not_found":
        reasons.append("no [aid -> file:line] citation emitted")

    return {"valid": True, "invalid_citations": invalid_citations, "status_ok": status_ok, "detected": detected, "hits": hits,
            "total_points": len(q["points"]), "point_detail": point_detail,
            "forbidden_hits": forbidden_hits,
            "carries_superseded": carries_superseded,
            "carries_conflict": carries_conflict,
            "fabrication": fabrication, "gave_up": gave_up,
            "evidence_hit": ev_hit, "citations": citations,
            "passed": passed, "reason": "; ".join(reasons) or "ok"}


def _forbidden_present(forbidden: str, answer: str, question: str = "") -> bool:
    """Does the answer actually commit the prohibited error?

    The card states prohibitions as Chinese sentences about a *claim* (e.g. "do
    not list Wesley Tate as the technical escalation contact"). Merely naming
    the person is required by the correct answer, so substring matching both
    false-positives constantly and misses paraphrases. Each prohibition is
    therefore judged semantically by the model: given the question, the answer,
    and the prohibition, did the answer assert the forbidden thing?
    """
    return _llm_forbidden_check(forbidden, answer, question)


_FORBIDDEN_JUDGE = """You are checking an answer against one explicitly prohibited error.

QUESTION: {question}

ANSWER:
{answer}

PROHIBITION (from the evaluation rubric):
{prohibition}

Decide whether the answer COMMITS this prohibited error -- i.e. it states,
asserts or implies the thing the prohibition forbids.

Careful:
- Merely MENTIONING a name, module or component does not commit the error.
  Naming someone while correctly describing their role is not a violation.
- Explicitly REFUTING or warning against the wrong claim is not a violation.
- If the answer says the material has no answer (not_found) and does not make
  up an owner, code meaning or location, that is not a violation.

Reply with JSON only: {{"violates": true/false, "quote": "<short quote or empty>", "reason": "<one sentence>"}}
"""

def _disk_cache() -> Path:
    d = RESULTS / ".judge_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(kind: str, payload: str) -> Path:
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    identity = json.dumps({"kind": kind, "payload": payload,
        "model": os.environ.get("LLM_JUDGE_MODEL") or os.environ.get("LLM_MODEL"),
        "schema": JUDGE_SCHEMA_VERSION, "version": JUDGE_PROMPT_VERSION,
        "prompts": [_POINT_JUDGE, _CARRIES_JUDGE, _FORBIDDEN_JUDGE]}, sort_keys=True)
    return _disk_cache() / f"{kind}-{hashlib.sha256(identity.encode()).hexdigest()}.json"


def _cached(kind: str, payload: str, compute):
    cp = _cache_path(kind, payload)
    if cp.exists():
        try:
            value = json.loads(cp.read_text(encoding="utf-8"))
            if ((kind == "points" and isinstance(value, list)
                 and all(type(v) is bool for v in value))
                    or (kind in ("forbidden", "carries") and type(value) is bool)):
                return value
        except (ValueError, OSError):
            pass
    value = compute()  # errors propagate; no error/fallback value is persisted
    pending = _pending_cache.get()
    if pending is not None:
        pending.append((cp, value))
    else:
        cp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def _llm_forbidden_check(prohibition: str, answer: str, question: str) -> bool:
    if not answer.strip():
        return False
    return _cached("forbidden", prohibition + "||" + question + "||" + answer,
                   lambda: _llm_forbidden_uncached(prohibition, answer, question))


def _llm_forbidden_uncached(prohibition: str, answer: str, question: str) -> bool:
    data = _judge_json(_FORBIDDEN_JUDGE.format(
        question=question, answer=answer, prohibition=prohibition))
    return _boolean(data, "violates")


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
        f"- Graph: `{db.GRAPH_NAME}`",
        f"- **PASS: {passed} / {len(qs)}**",
        f"- Invalid judgements: {sum(not r['judgement']['valid'] for r in results)}",
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
            f"## Q{q['qid']} — {'INVALID' if not j['valid'] else 'PASS' if j['passed'] else 'FAIL'}",
            "",
            f"**Question**: {q['question']}",
            "",
            f"- Expected state: `{q['expected_status']}` / detected: `{j['detected']}`"
            f" (supersession chain: {'yes' if j['carries_superseded'] else 'no'},"
            f" conflict laid out: {'yes' if j['carries_conflict'] else 'no'})",
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
            f"**Verdict**: {'INVALID' if not j['valid'] else 'PASS' if j['passed'] else 'FAIL'} — {j['reason']}",
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


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    from dotenv import load_dotenv

    ap = argparse.ArgumentParser(description="Run the 17 gold-card questions")
    ap.add_argument("--judge-only", action="store_true",
                    help="reuse cached answers, re-run only the judging step")
    args = ap.parse_args(argv)

    load_dotenv(ROOT / ".env")
    model = os.environ.get("LLM_MODEL")

    qs = parse_gold()
    if len(qs) != 17:
        print(f"WARNING: parsed {len(qs)} questions, expected 17", file=sys.stderr)
    RESULTS.mkdir(parents=True, exist_ok=True)
    answers_file = RESULTS / "answers_cache.json"
    cache = (json.loads(answers_file.read_text(encoding="utf-8"))
             if answers_file.exists() else {})

    results = []
    for q in qs:
        trace: list[dict] = []
        if args.judge_only and q["qid"] in cache:
            res = cache[q["qid"]]
            print(f"Q{q['qid']}: cached ({len(res.get('answer') or '')} chars)",
                  flush=True)
        else:
            res = agent.ask(q["question"], trace=trace)
            cache[q["qid"]] = res
            answers_file.write_text(json.dumps(cache, ensure_ascii=False),
                                    encoding="utf-8")
            print(f"Q{q['qid']}: {res['tool_calls']} calls, "
                  f"{len(res['answer'] or '')} chars", flush=True)
        # citations really present in the emitted answer text
        citations = sorted(set(re.findall(r"\[([^\]\n]{3,120})\]",
                                          res.get("answer") or "")))
        results.append({"result": res, "inspect": {"citations": citations}})

    for q, r in zip(qs, results):
        r["judgement"] = judge(q, r)
        print(f"Judged Q{q['qid']}: "
              f"{'INVALID' if not r['judgement']['valid'] else 'PASS' if r['judgement']['passed'] else 'FAIL'}",
              flush=True)

    stats = db.stats()
    report = render(qs, results, stats, model or "")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS / "latest.md").write_text(report, encoding="utf-8")
    (RESULTS / f"{stamp}.md").write_text(report, encoding="utf-8")

    import subprocess
    provenance = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"], cwd=ROOT)).hexdigest(),
        "corpus_sha256": hashlib.sha256(b"".join(
            f.relative_to(ROOT).as_posix().encode() + f.read_bytes()
            for f in sorted((ROOT / "corpus").rglob("*")) if f.is_file()
            and f.suffix in (".md", ".json", ".csv"))).hexdigest(),
        "graph": db.GRAPH_NAME, "model": model,
        "graph_sha256": hashlib.sha256(json.dumps(
            db.g().query("MATCH (s:Entity)-[r]->(o:Entity) "
                         "RETURN s.id, type(r), o.id, properties(r), r.aid ORDER BY r.aid").result_set,
            ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
        "judge_model": os.environ.get("LLM_JUDGE_MODEL") or model,
        "judge_schema": JUDGE_SCHEMA_VERSION, "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "stats": stats, "questions": qs, "results": results}
    (RESULTS / f"{stamp}.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
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
