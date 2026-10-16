"""Fast mode: retrieve from the graph first, then compose with one LLM call.

The agent loop (agent.ask) lets the model choose every tool call, which costs
one LLM round trip per step. Here the code does the retrieval itself:

1. candidate entities from a fulltext search over the question's words, plus
   source lines sharing the question's informative words;
2. Jev (TypeSafe System One) decides which candidates are worth reading -- one
   fast typed decision request instead of several reasoning-model turns;
   without a TYPESAFE_API_KEY the fulltext ranking decides;
3. read_knowledge on the chosen entities;
4. one LLM call writes the answer, which then goes through the same status,
   citation-grounding and not_found checks as the agent loop.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable

from . import tools
from .agent import SYSTEM, LANG_NOTE, MAX_CALLS, MAX_RELATION_QUERIES, _arg_str, _finalize, _summary, \
    _openai_client, _templates_for, _StepLog
from .citations import citation_pairs, compact_tool_result

JEV_URL = "https://api.typesafe.ai/v1/systemone"
MAX_READS = 5
JEV_THRESHOLD = 0.35

_STOP = {"the", "and", "for", "our", "what", "where", "when", "who", "how", "should",
         "can", "this", "that", "with", "they", "are", "not", "which", "does", "there",
         "their", "them", "from", "any", "into", "out", "about", "have", "has", "was",
         "were", "will", "would", "could", "need", "want", "i'm", "it's", "use", "new",
         "find", "get", "ask", "asks", "tell", "do", "did", "if", "is", "it", "my", "me"}


WHO = re.compile(r"\b(who|whom|ping|escalat\w*|contact|reach|ask|expert\w*|owner)\b|谁|找", re.I)


def _keywords(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", question)
    seen, out = set(), []
    for w in words:
        k = w.casefold()
        if k not in _STOP and k not in seen:
            seen.add(k)
            out.append(w)
    return out


def _candidates(question: str, limit: int = 16) -> list[dict]:
    words = _keywords(question)
    if not words:
        return []
    found = {}
    # One search per question word; an entity named by several words ranks first.
    for w in words:
        term = re.sub(r"[^A-Za-z0-9]", "", w)
        if not term:
            continue
        try:
            rows = tools.g().query(tools.TEMPLATES["fulltext_search"],
                                   {"q": term, "limit": 8}).result_set
        except Exception:  # noqa: BLE001 - one bad term must not sink the rest
            rows = []
        for i, k, n, s in rows:
            c = found.setdefault(i, {"id": i, "kind": k, "name": n, "score": 0.0, "hits": 0})
            c["score"] += float(s)
            c["hits"] += 1
    # Exact identifiers (MOD-01, TCK-1001, error strings) by id substring.
    for w in words:
        if re.search(r"\d|[-_]", w):
            for i, k, n, s in tools.g().query(tools.TEMPLATES["id_prefix"],
                                              {"q": w, "limit": 4}).result_set:
                found.setdefault(i, {"id": i, "kind": k, "name": n, "score": 0.0, "hits": 0})["hits"] += 5
    return sorted(found.values(), key=lambda c: (-c["hits"], -c["score"]))[:limit]


def _jev_pick(question: str, cands: list[dict]) -> tuple[list[dict], str]:
    """Jev scores each candidate; returns the chosen ones and who decided."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if key and cands:
        import requests
        questions = {f"c{i}": {"type": "noul", "instructions":
                     f"Would reading the knowledge-graph entity '{c['name']}' ({c['kind']}, id {c['id']}) "
                     "help answer the question?"} for i, c in enumerate(cands)}
        try:
            r = requests.post(JEV_URL, headers={"Authorization": "Bearer " + key},
                              json={"model": "jev-latest", "state": "Question: " + question,
                                    "questions": questions}, timeout=8)
            answers = r.json().get("answers", {}) if r.status_code == 200 else {}
            scored = []
            for k, v in answers.items():
                p = v.get("noul", v.get("probability"))
                if isinstance(p, (int, float)) and k[1:].isdigit() and int(k[1:]) < len(cands):
                    scored.append((float(p), cands[int(k[1:])]))
            if scored:
                scored.sort(key=lambda x: -x[0])
                chosen = [c | {"jev": round(p, 2)} for p, c in scored if p >= JEV_THRESHOLD][:MAX_READS]
                return chosen or [scored[0][1] | {"jev": round(scored[0][0], 2)}], "jev"
        except Exception:  # noqa: BLE001 - fall back to the fulltext ranking
            pass
    return cands[:4], "fulltext"


def _to_english(question: str, model: str | None = None) -> str:
    """One short translation call; on any failure search with the original text."""
    try:
        resp = _openai_client().chat.completions.create(
            model=model or os.environ.get("FAST_MODEL") or os.environ.get("LLM_MODEL"), temperature=0.0,
            messages=[{"role": "user", "content": "Translate this IT operations question into "
                       "English. Reply with the translation only.\n\n" + question}])
        text = (resp.choices[0].message.content or "").strip()
        return text if _keywords(text) else question
    except Exception:  # noqa: BLE001 - translation is best-effort
        return question


def ask_fast(question: str, model: str | None = None, trace: list | None = None,
             lang: str | None = None, on_step: Callable[[dict], None] | None = None) -> dict:
    """Retrieve and answer; optionally publish steps before the final response."""
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    log = _StepLog(on_step)
    tool_results: list[dict] = []

    def record(name: str, args: dict, result: dict) -> dict:
        result = compact_tool_result(result)
        result["citations"] = [f"[{aid} → {loc}]"
                               for aid, loc in sorted(citation_pairs([result]))]
        tool_results.append(result)
        log.append({"step": len(log) + 1, "tool": name, "args": args,
                    "args_str": _arg_str(args), "status": result.get("status"),
                    "template": _templates_for(name, args, result),
                    "summary": _summary(result)})
        return result

    # The sources are English: Chinese words match nothing (one stray "OOM" is not
    # enough), so search with an English rendering and answer the question as asked.
    search_q = _to_english(question, model) if re.search(r"[一-鿿]", question) else question
    if search_q != question:
        log.append({"step": len(log) + 1, "tool": "translate", "args": {"english": search_q},
                    "args_str": "", "status": "ok", "template": ["(no Cypher: translation)"],
                    "summary": {"name": search_q}})
    pre = tools.related_source_lines(search_q)
    if pre:
        record("source_lines", {"question_words": " ".join(_keywords(search_q))},
               {"status": "ok", "source_evidence": pre})
    cands = _candidates(search_q)
    timings["retrieve"] = time.perf_counter() - t0

    t = time.perf_counter()
    chosen, decided_by = _jev_pick(search_q, cands)
    timings["decide"] = time.perf_counter() - t
    if log:
        log[-1]["candidates"] = len(cands)
    log.append({"step": len(log) + 1, "tool": f"decide ({decided_by})",
                "args": {"candidates": [c["id"] for c in cands],
                         "chosen": [f"{c['id']} ({c.get('jev', '')})".replace(" ()", "") for c in chosen]},
                "args_str": "", "status": "ok", "template": ["(no Cypher: typed decision)"]})

    t = time.perf_counter()
    read = set()
    for c in chosen:
        record("read_knowledge", {"id": c["id"]}, tools.read_knowledge(c["id"]))
        read.add(c["id"])
    timings["read"] = time.perf_counter() - t

    # One hop further: the answer often sits on a neighbour whose name shares no
    # word with the question (the module's knowledge base, the rule that
    # replaced the one asked about). Jev picks among the neighbours just read.
    t = time.perf_counter()
    hop = {}
    for res in tool_results:
        for a in res.get("assertions", []):
            o = a["other"]
            if o["id"] not in read:
                hop.setdefault(o["id"], {"id": o["id"], "kind": o["kind"], "name": o["name"] or o["id"]})
    hop_cands = list(hop.values())[:24]
    if hop_cands:
        picked, by = _jev_pick(search_q, hop_cands)
        picked = [c for c in picked if c.get("jev", 1) >= 0.5][:3]
        log.append({"step": len(log) + 1, "tool": f"decide hop ({by})",
                    "args": {"neighbours": len(hop_cands),
                             "chosen": [f"{c['id']} ({c.get('jev', '')})" for c in picked]},
                    "args_str": "", "status": "ok", "template": ["(no Cypher: typed decision)"]})
        for c in picked:
            record("read_knowledge", {"id": c["id"]}, tools.read_knowledge(c["id"]))
    timings["hop"] = time.perf_counter() - t

    # "Who should I ask" about a module: the official owner is one read away; who
    # has actually fixed it sits two hops off (Ticket-ABOUT->Module, Ticket-HANDLED_BY->Person).
    if WHO.search(question) or WHO.search(search_q):
        t = time.perf_counter()
        mods = [r["entity"]["id"] for r in tool_results
                if (r.get("entity") or {}).get("kind") == "Module"][:2]
        if not mods:  # no module read: take the one the read entities point at most
            seen: dict[str, int] = {}
            for r in tool_results:
                for a in r.get("assertions", []):
                    if a["other"]["kind"] == "Module":
                        seen[a["other"]["id"]] = seen.get(a["other"]["id"], 0) + 1
            mods = sorted(seen, key=lambda m: -seen[m])[:1]
        if not mods:  # or the module the name search found but Jev left unread
            mods = [c["id"] for c in cands if c.get("kind") == "Module"][:1]
        for m in mods:
            record("module_expertise", {"module_id": m}, tools.module_expertise(m))
        timings["expertise"] = time.perf_counter() - t

    t = time.perf_counter()
    log.compose()
    client = _openai_client()
    prompt = SYSTEM.format(max_calls=MAX_CALLS, max_rel=MAX_RELATION_QUERIES)
    if lang in LANG_NOTE:
        prompt += "\n" + LANG_NOTE[lang]
    evidence = json.dumps(tool_results, ensure_ascii=False, default=str)
    answer = ""
    for _ in range(2):
        try:
            resp = client.chat.completions.create(
                model=model or os.environ.get("FAST_MODEL") or os.environ.get("LLM_MODEL"), temperature=0.0,
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": question},
                          {"role": "user", "content":
                           "The graph has already been queried for you; no tools are available. "
                           "Answer only from these tool results and cite them exactly:\n" + evidence}])
            answer = resp.choices[0].message.content or ""
            if answer:
                break
        except Exception:  # noqa: BLE001 - one retry on provider errors
            continue
    if not answer:
        answer = "(answer generation failed after retries: no response from the model provider)"
    timings["compose"] = time.perf_counter() - t

    out = _finalize(question, answer, tool_results, log, trace)
    out["mode"] = "fast"
    out["decided_by"] = decided_by
    out["timings"] = {k: round(v, 2) for k, v in timings.items()} | {
        "total": round(time.perf_counter() - t0, 2)}
    return out
