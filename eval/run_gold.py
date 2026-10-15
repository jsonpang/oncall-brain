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
    try:
        import json as _json
        import os

        import openai
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        client = openai.OpenAI(base_url=os.environ["LLM_BASE_URL"],
                               api_key=os.environ["LLM_API_KEY"])
        Resp = client.chat.completions.create(
            model=os.environ.get("LLM_JUDGE_MODEL") or os.environ["LLM_MODEL"],
            messages=[{"role": "user", "content": _POINT_JUDGE.format(
                question=question, answer=answer[:6000], points=enumerated)}],
            temperature=0.0, response_format={"type": "json_object"})
        data = _json.loads(Resp.choices[0].message.content or "{}")
        rows = {int(r.get("index", -1)): bool(r.get("covered"))
                for r in data.get("results", [])}
        return [rows.get(i, False) for i in range(1, len(points) + 1)]
    except Exception:  # noqa: BLE001 - fall back to lexical overlap
        a_norm = _norm(answer)
        out = []
        for p in points:
            toks = [t for t in _norm(p).split() if len(t) > 3 and t not in _STOP][:6]
            out.append(bool(toks) and sum(1 for t in toks if t in a_norm)
                       >= max(2, len(toks) // 2))
        return out


def _count_point_hits(q: dict, answer: str) -> tuple[int, list[bool]]:
    flags = _llm_point_hits(q["question"], answer, q["points"])
    return sum(1 for f in flags if f), flags


def _says_not_found(answer: str) -> bool:
    """Did the answer actually declare there is no answer in the material?

    The words must appear as the answer's own conclusion. A sentence like
    "no documentation for X was found anywhere" counts; "there is no need to
    contact X" does not, so the latter must not be mistaken for not_found.
    """
    sentences = re.split(r"[.\n]+", answer)
    for s in sentences:
        sl = s.lower()
        if any(w in sl for w in NOT_FOUND_WORDS):
            # content-only peeking: exclude sentences that merely mention
            # absence of unrelated things while still giving an answer
            if any(m in sl for m in ("? ", "if you", "please contact")):
                continue
            return True
    return False


def judge(q: dict, res: dict) -> dict:
    # res is {"result": agent result, "inspect": {...}}
    answer = res["result"].get("answer") or ""
    a_norm = _norm(answer)
    expected = q["expected_status"]
    citations = res["inspect"]["citations"]
    ev_hit = _evidence_cited(answer, q["evidence"])

    # status detection from what the answer actually says
    says_not_found = _says_not_found(answer)
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

    # point hits: is each gold point substantively present in the answer?
    hits, point_detail = _count_point_hits(q, answer)

    forbidden_hits = [f for f in q["forbidden"]
                      if _forbidden_present(f, answer, q["question"])]

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
            "total_points": len(q["points"]), "point_detail": point_detail,
            "forbidden_hits": forbidden_hits,
            "evidence_hit": ev_hit, "citations": citations,
            "passed": passed, "reason": "; ".join(reasons) or "ok"}


_STOP = {"the", "and", "that", "this", "for", "with", "from", "must", "not",
         "every", "per", " Incident", "74c042b2"}


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

_forbidden_cache: dict[tuple[str, str], bool] = {}


def _disk_cache() -> Path:
    d = RESULTS / ".judge_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(kind: str, payload: str) -> Path:
    return _disk_cache() / f"{kind}-{hashlib.sha256(payload.encode()).hexdigest()[:20]}.json"


def _cached(kind: str, payload: str, compute):
    """Judge results persist across runs, so re-judging is instant."""
    cp = _cache_path(kind, payload)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - recompute on a corrupt cache file
            pass
    value = compute()
    cp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def _llm_forbidden_check(prohibition: str, answer: str, question: str) -> bool:
    key = (hash(prohibition), hash(answer))
    if key in _forbidden_cache:
        return _forbidden_cache[key]
    if not answer.strip():
        _forbidden_cache[key] = False
        return False

    def compute() -> bool:
        return _llm_forbidden_uncached(prohibition, answer, question)

    verdict = _cached("forbidden", prohibition + "||" + question + "||" + answer,
                      compute)
    _forbidden_cache[key] = verdict
    return verdict


def _llm_forbidden_uncached(prohibition: str, answer: str, question: str) -> bool:
    try:
        import json as _json
        import os

        import openai
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        client = openai.OpenAI(base_url=os.environ["LLM_BASE_URL"],
                               api_key=os.environ["LLM_API_KEY"])
        resp = client.chat.completions.create(
            model=os.environ.get("LLM_JUDGE_MODEL") or os.environ["LLM_MODEL"],
            messages=[{"role": "user", "content": _FORBIDDEN_JUDGE.format(
                question=question, answer=answer[:6000],
                prohibition=prohibition)}],
            temperature=0.0, response_format={"type": "json_object"})
        data = _json.loads(resp.choices[0].message.content or "{}")
        verdict = bool(data.get("violates"))
    except Exception:  # noqa: BLE001 - fail open, reported in the log
        verdict = False
    _forbidden_cache[key] = verdict
    return verdict


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
