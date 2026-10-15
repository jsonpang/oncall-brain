"""Tool-calling agent loop over the 4 read-only tools.

Budget per design doc §6: at most 8 tool calls and 6 relation queries per
question. The agent may only use IDs that came back from a tool result. It
answers in the mandated order and, when nothing is visible, says not_found
instead of guessing.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import tools

ROOT = Path(__file__).resolve().parent.parent
MAX_CALLS = 8
MAX_RELATION_QUERIES = 6

SYSTEM = """You answer operations questions for the fictional company AetherPulse
using a knowledge graph of its support organisation. You have four read-only tools.

HARD RULES:
1. Use ONLY ids returned by a previous tool call. Never invent an id.
2. Budget: at most {max_calls} tool calls and {max_rel} relation queries total.
   Spend it well: go straight to search_knowledge with a short distinctive
   keyword (a module name, person surname, form name, or error string), then
   read the entity. Do NOT call describe_ontology again if you already know the
   predicates, and do not retry a failed search with near-identical wording --
   each call is non-refundable.
3. Prefer read_knowledge on a promising entity over repeated searching: a single
   read shows every visible assertion touching it, including all predicates.
   Use query_relations when you need to follow a chain past the first hop.
4. Facts come only from assertions returned by the tools. If an assertion is not
   visible, it is not known -- do not fill it in from general knowledge.
5. Answer the question asked and name the specifics the questioner needs: the
   actual person, the actual channel, form or path, the actual deadline and
   number. A correct-but-generic answer ("use the standard procedure") is a
   failure. If several people are involved, say which one does what.
5. MAINTAINED_BY proves only who curates a document. It is NEVER an escalation or troubleshooting target. The module escalation owner is TECH_LEAD_OF.
6. When several visible values occupy the same slot, present them all ranked by authority (formal_rule > practice > observation) and label it a conflict. Do not silently pick one.
7. A rule targeted by SUPERSEDES is superseded: give the current rule, then state the old one, the replacement, and the effective date.
8. A rule with a condition (e.g. severity=P3) that the question does not supply is insufficient_context: answer with the conditional rule AND ask for the missing condition.
9. If nothing relevant is visible, answer not_found -- state plainly that the material has no answer, and say who/what to ask next. Never speculate.

ANSWER FORMAT: first line MUST be Status: <applicable|insufficient_context|conflict|superseded|not_found>.
Use the answer_status returned by relevant tools, never infer status from prose.
Then use these markdown sections (omit a section only when empty):
## Next step
Concrete action the asker should take now. Concrete names, paths, forms, deadlines.
## Basis
Each claim on its own line ending with the citation `[aid -> file:line]`. Use the
`aid` and the evidence locator exactly as returned (source:locator).
## Actual common practice
Marked observation/practice when it differs from or sits beside the official path, with its risk and citation.
## Conflicts / Superseded
Conflicting values side by side with authority levels; superseded rules with replacement + effective date.
## Gaps
What has no answer here, and who to ask to close it.

Prefer answering in the language of the question. Cite at least one piece of
evidence for every factual claim you make.
"""


def _openai_client():
    import openai
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    return openai.OpenAI(base_url=os.environ["LLM_BASE_URL"],
                         api_key=os.environ["LLM_API_KEY"])


def _tool_specs() -> list[dict]:
    return [{"type": "function", "function": {
        "name": name,
        "description": spec["description"],
        "parameters": spec["parameters"],
    }} for name, spec in tools.TOOLS.items()]


def _arg_str(args: dict) -> str:
    """Compact, deterministic rendering of call arguments for the eval log."""
    parts = []
    for k, v in sorted((args or {}).items()):
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False, sort_keys=True)
        parts.append(f"{k}={v}")
    return ", ".join(parts)


def answer_status(results: list[dict]) -> str:
    """State comes from tool contracts; absent/failed evidence is incomplete."""
    states = {r.get("answer_status") for r in results}
    for state in ("conflict", "superseded", "insufficient_context", "applicable", "not_found"):
        if state in states:
            return state
    return "insufficient_context"


def ask(question: str, model: str | None = None, trace: list | None = None,
        max_calls: int = MAX_CALLS) -> dict:
    """Run the tool-calling loop for one question.

    `trace` (if given) collects the real call sequence: tool name, arguments,
    result status, and the Cypher template each call used.
    """
    client = _openai_client()
    model = model or os.environ.get("LLM_MODEL")
    messages = [
        {"role": "system",
         "content": SYSTEM.format(max_calls=max_calls, max_rel=MAX_RELATION_QUERIES)},
        {"role": "user", "content": question},
    ]
    log: list[dict] = []
    tool_results: list[dict] = []
    relation_queries = 0
    answer = ""
    stop_reason = "completed"

    for step in range(max_calls):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=_tool_specs(),
            tool_choice="auto", temperature=0.0)
        msg = resp.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            answer = msg.content or ""
            break

        for call in msg.tool_calls:
            if len(log) >= max_calls:
                stop_reason = "max_calls_reached"
                break
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "query_relations":
                relation_queries += 1
                if relation_queries > MAX_RELATION_QUERIES:
                    result = {"status": "error",
                              "error": "relation-query budget exhausted"}
                else:
                    result = tools.call_tool(name, args)
            else:
                result = tools.call_tool(name, args)
            tool_results.append(result)
            entry = {"step": step + 1, "tool": name, "args": args,
                     "args_str": _arg_str(args),
                     "status": result.get("status"),
                     "template": _templates_for(name, args, result)}
            log.append(entry)
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False,
                                                   default=str)})
        if len(log) >= max_calls:
            # Any tool_calls we broke on still need a reply, or the next
            # request is rejected as an unanswered tool call.
            answered = {m["tool_call_id"] for m in messages
                        if isinstance(m, dict) and m.get("role") == "tool"}
            for call in msg.tool_calls:
                if call.id not in answered:
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": json.dumps(
                                         {"status": "error",
                                          "error": "tool-call budget exhausted"},
                                         ensure_ascii=False)})
            stop_reason = "max_calls_reached"
            break
    else:
        stop_reason = "max_calls_reached"

    if not answer:
        # Always close out with a written answer, whether the budget ran out or
        # the last turn ended on tool calls: an empty answer is useless to the
        # reader and unjudgeable by the eval.
        prompt = ("Tool budget is exhausted. Write the final answer now using "
                  "only what the tool results showed; put anything unverified "
                  "in Gaps.")
        messages.append({"role": "user", "content": prompt})
        # Some providers reject a long accumulated tool transcript with a 400.
        # Retry once with tools disabled, then once with a trimmed transcript,
        # before giving up -- losing a question to a provider quake should not
        # be read as "the graph had no answer".
        attempts = [
            dict(model=model, messages=messages, temperature=0.0),
            dict(model=model, messages=[messages[0], {"role": "user",
                 "content": question}, {"role": "user", "content":
                 "Actual tool evidence: " + json.dumps(tool_results, ensure_ascii=False)
                 + "\n" + prompt}],
                 temperature=0.0),
        ]
        for kwargs in attempts:
            try:
                resp = client.chat.completions.create(**kwargs)
                answer = resp.choices[0].message.content or ""
                if answer:
                    break
            except Exception:  # noqa: BLE001 - try the next strategy
                continue
        if not answer:
            answer = ("(answer generation failed after retries: no response from "
                      "the model provider)")

    if trace is not None:
        trace.extend(log)
    state = answer_status(tool_results)
    answer = re.sub(r"\AStatus:[^\n]*(?:\n|$)", "", answer)
    answer = f"Status: {state}\n" + answer
    return {"question": question, "answer": answer, "trace": log,
            "tool_results": tool_results, "answer_status": state,
            "tool_calls": len(log), "relation_queries": relation_queries,
            "stop_reason": stop_reason}


def _templates_for(name: str, args: dict, result: dict) -> list[str]:
    """Which server-side Cypher templates this call ran (for the eval log)."""
    if name == "describe_ontology":
        return ["(schema only, no Cypher)"]
    if name == "search_knowledge":
        if result.get("status") == "error":
            return ["(no query)"]
        keys = ["fulltext_search_kind" if args.get("kind") else "fulltext_search"]
        keys.append("id_prefix_kind" if args.get("kind") else "id_prefix")
        return keys
    if name == "read_knowledge":
        return ["read_entity", "read_neighbors", "superseded_targets",
                "slot_claims", "evidence_by_id", "(relationship evidence join)"]
    if name == "query_relations":
        if result.get("status") == "error":
            return ["(rejected before query)"]
        return ["traverse", "superseded_targets", "slot_claims", "evidence_by_id"]
    return []
