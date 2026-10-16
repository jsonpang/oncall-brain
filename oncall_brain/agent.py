"""Tool-calling agent loop over the 4 read-only tools.

Budget: at most 8 tool calls and 6 relation queries per
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
from .citations import citation_pairs, compact_tool_result, normalize_citations, parse_citation

ROOT = Path(__file__).resolve().parent.parent
MAX_CALLS = 8
MAX_RELATION_QUERIES = 6

SYSTEM = """You answer operations questions for the fictional company AetherPulse
using a knowledge graph of its support organisation. You have five read-only tools.

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
   When a graph assertion (an entry under "assertions" or "paths") carries a
   fact, cite that assertion's aid with its evidence line, not only the bare
   source line: the assertion is what proves the relation. For a list (channels,
   fields, people), cite the assertion for each item that has one. Bare source
   lines are for facts no assertion carries.
5. Answer the question asked and name the specifics the questioner needs: the
   actual person, the actual channel, form or path, the actual deadline and
   number. A correct-but-generic answer ("use the standard procedure") is a
   failure. If several people are involved, say which one does what.
   When the evidence enumerates items (mandatory fields, channels, steps,
   deadlines, penalties), list every one of them by its exact name, not a
   subset or a paraphrase, and include the stated penalty or audit risk.
5. MAINTAINED_BY proves only who curates a document. It is NEVER an escalation or troubleshooting target. The module escalation owner is TECH_LEAD_OF.
   module_expertise lists who has actually handled a module's past tickets
   (HANDLED_BY on tickets ABOUT the module). When the question is whom to ask or
   escalate to, name the TECH_LEAD_OF owner first, then add who has handled this
   module's tickets in practice, citing each HANDLED_BY aid with its ticket id.
   In ## Answer, sentence one names the owner and sentence two names the people
   who have handled it, each with one HANDLED_BY citation.
   This yields to a yes/no or conflict answer: when the question asks whether to
   do something or its premise is contradicted, ## Answer answers that first and
   the handlers go under ## Basis instead.
   A handler is history, never the owner; skip this when no result is returned.
6. Conflict requires the SAME subject and question slot, overlapping conditions,
   and mutually exclusive values. Different predicates, multi-valued facts and
   different granularity are not conflicts. Use the returned conflict records
   only when they concern the question; incidental neighbour conflicts do not
   determine the answer status. Rank actual conflicts by authority.
   A person's belief, proposal or routing assumption in the evidence that an
   authoritative source or role record contradicts is also a conflict when the
   question repeats or relies on it. Likewise when the question's own premise
   (a claimed rule, deadline or person to contact) is contradicted by the
   evidence, the status is conflict, not applicable: put both sides, each cited, under
   ## Conflicts / Superseded and give the authoritative answer as Next step.
7. A rule targeted by SUPERSEDES is superseded: give the current rule, then state the old one, the replacement, and the effective date.
8. A rule with a condition (e.g. severity=P3) that the question does not supply is insufficient_context: answer with the conditional rule AND ask for the missing condition.
9. If the requested facts are absent, answer not_found. List only what was
   searched and which requested facts are missing. Do not suggest a plausible
   owner, procedure, or related module. An owner of a similar module does not
   establish ownership of an unknown component or a remedy for an unknown error.
   When source lines discuss this very item or error (it is out of scope, no
   runbook exists, how to reply, where to log it, who is already handling the
   incident), keep Status: not_found and still give that handling, each line cited.

ANSWER FORMAT: first line MUST be Status: <applicable|insufficient_context|conflict|superseded|not_found>.
Choose a status supported by tools relevant to the question. You may report
not_found even when a tool returned unrelated applicable facts.
Then use these markdown sections (omit a section only when empty):
## Answer
At most two short sentences that directly answer the question asked -- the
conclusion a reader can act on -- each ending with its citation. Omit when not_found.
If the question is yes/no ("Should I ...?", "Can I ...?"), sentence one starts
with the plain answer -- No / Yes / Not yet -- and names who or what to use instead.
For insufficient_context, sentence two says exactly which condition is missing
and its possible values (e.g. "Missing: the ticket severity, P1-P4").
## Next step
Concrete action the asker should take now. Concrete names, paths, forms, deadlines.
## Basis
Each claim on its own line ending with the citation `[aid \u2192 file:line]`. Use the
`aid` and the evidence locator exactly as returned (source:locator).
## Actual common practice
Marked observation/practice when it differs from or sits beside the official path, with its risk and citation.
## Conflicts / Superseded
Conflicting values side by side with authority levels; superseded rules with replacement + effective date.
## Gaps
What has no answer here, and who to ask to close it.

Prefer answering in the language of the question. Cite at least one piece of
evidence for every factual line in EVERY section, including Next step. Do not
split a claim and its citation across lines. Omit uncited introductions and
advice. Only headings, empty lines and questions asking for missing context
may omit citations. Never duplicate the locator inside a citation.
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


def answer_status(results: list[dict], proposed: str | None = None) -> str:
    """State comes from tool contracts; absent/failed evidence is incomplete."""
    states = {r.get("answer_status") for r in results}
    if proposed == "not_found":
        return "not_found"
    if proposed == "applicable" and any(r.get("assertions") or r.get("paths")
                                       or r.get("source_evidence") for r in results):
        return proposed
    if proposed and proposed in states:
        return proposed
    for state in ("conflict", "superseded", "insufficient_context", "applicable", "not_found"):
        if state in states:
            return state
    return "insufficient_context"


def _missing_identifiers(question: str, results: list[dict]) -> list[str]:
    identifiers = re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+){2,}\b|\b0x[0-9A-Fa-f]+\b", question)
    # Opaque component/error identifiers, not ordinary hyphenated prose.
    identifiers = [s for s in identifiers if "_" in s or re.search(r"[A-Z0-9]", s)]
    facts = [{k: r[k] for k in ("entity", "candidates", "assertions", "paths", "evidence", "source_evidence")
              if k in r} for r in results]
    visible = json.dumps(facts, ensure_ascii=False).casefold()
    return sorted({s for s in identifiers if s.casefold() not in visible})


def _grounded_answer(answer: str, supported: set[tuple[str, str]]) -> tuple[str, int]:
    """Keep only lines backed by a citation returned for this question.

    Unsupported citations are stripped; a line left without any supported
    citation is dropped instead of discarding the whole answer.
    """
    kept, facts = [], 0
    for raw in answer.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line in ("---", "***"):
            kept.append(raw)
            continue
        good = 0
        def check(match):
            nonlocal good
            c = match[1]
            if not ("corpus/" in c or "->" in c or "\u2192" in c):
                return match[0]
            if parse_citation(c) in supported:
                good += 1
                return match[0]
            return ""
        line = re.sub(r"\s*\[([^\]\n]+)\]", check, raw)
        if good or line.strip().endswith(("?", "\uff1f")):
            kept.append(line)
            facts += bool(good)
    text = re.sub(r"(?m)^##[^\n]*(?:\n|\Z)(?=\s*(?:##|\Z))", "", "\n".join(kept))
    return text.strip("\n"), facts


_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "my", "our",
         "what", "where", "when", "who", "how", "should", "is", "it", "i", "we", "do"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.casefold()) if w not in _STOP and len(w) > 2}


def _supersession_lines(question: str, results: list[dict],
                        supported: set[tuple[str, str]]) -> list[str]:
    """Graph-recorded supersessions of rules the question is about."""
    lines, asked = [], _words(question)
    for r in results:
        entity = r.get("entity") or {}
        if not r.get("superseded") or len(_words(entity.get("name", "")) & asked) < 2:
            continue
        for s in r["superseded"]:
            locs = sorted(loc for aid, loc in supported if aid == s.get("aid"))
            if locs:
                lines.append(f"{entity['name']} is superseded by {s['superseded_by']}"
                             + (f", effective {s['effective']}" if s.get("effective") else "")
                             + f" [{s['aid']} → {locs[0]}].")
    return lines


def _identifiers(question: str) -> list[str]:
    ids = re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+){2,}\b|\b0x[0-9A-Fa-f]+\b", question)
    return [s for s in ids if "_" in s or re.search(r"[A-Z0-9]", s)]


def _item_discussion(question: str, results: list[dict], window: int = 8) -> set[str]:
    """Locators within `window` lines of a source line naming the question's identifier."""
    ids = [s.casefold() for s in _identifiers(question)]
    if not ids:
        return set()
    lines = {}
    for r in results:
        for e in r.get("source_evidence", []) + r.get("evidence", []):
            if isinstance(e, dict) and "source" in e:
                lines[(e["source"], int(e["locator"]))] = (e.get("excerpt") or "").casefold()
    hits = [(s, n) for (s, n), text in lines.items() if any(i in text for i in ids)]
    return {f"{s}:{n}" for (s, n) in lines
            if any(s == hs and abs(n - hn) <= window for hs, hn in hits)}


def _cited_conflict(answer: str) -> bool:
    """A model-reported conflict counts only when its Conflicts section cites
    two or more distinct source lines (the contradicting statements)."""
    section = re.search(r"(?ms)^##\s*Conflicts[^\n]*\n(.*?)(?=^##|\Z)", answer)
    if not section:
        return False
    locators = {parse_citation(c)[1] for c in re.findall(r"\[([^\]\n]+)\]", section[1])
                if parse_citation(c)}
    return len(locators) >= 2


def _not_found_answer(question: str, log: list[dict], missing: list[str]) -> str:
    searched = [f"- {entry['tool']}({_arg_str(entry['args'])})" for entry in log]
    return ("## Searched\n" + ("\n".join(searched) or "- No successful retrieval.")
            + "\n## Missing\n- Requested facts: " + question.replace("\n", " ")
            + ("\n- Unretrieved identifiers: " + ", ".join(missing) if missing else "")
            + "\n- The retrieved evidence does not establish the requested answer.")


LANG_NOTE = {"zh": ("Write the answer text in Simplified Chinese. Keep the markdown headings, the "
                    "Status line, people's names, ids, paths, form and channel names, and every "
                    "citation exactly as they are, in English.")}


def ask(question: str, model: str | None = None, trace: list | None = None,
        max_calls: int = MAX_CALLS, lang: str | None = None) -> dict:
    """Run the tool-calling loop for one question.

    `trace` (if given) collects the real call sequence: tool name, arguments,
    result status, and the Cypher template each call used.
    """
    client = _openai_client()
    model = model or os.environ.get("LLM_MODEL")
    messages = [
        {"role": "system",
         "content": SYSTEM.format(max_calls=max_calls, max_rel=MAX_RELATION_QUERIES)
         + ("\n" + LANG_NOTE[lang] if lang in LANG_NOTE else "")},
        {"role": "user", "content": question},
    ]
    log: list[dict] = []
    tool_results: list[dict] = []
    relation_queries = 0
    answer = ""
    stop_reason = "completed"

    # Source lines sharing the question's informative words, fetched before the
    # loop so official passages the model would not think to search for (and
    # the list items that follow them) are in view. Not counted in the budget.
    try:
        pre = tools.related_source_lines(question)
    except Exception:  # noqa: BLE001 - retrieval aid only; the tools still work
        pre = []
    if pre:
        result = compact_tool_result({"status": "ok", "source_evidence": pre})
        result["citations"] = [f"[{aid} → {locator}]"
                               for aid, locator in sorted(citation_pairs([result]))]
        tool_results.append(result)
        messages.append({"role": "user", "content":
            "Source lines that share words with the question (retrieved automatically; "
            "they may be irrelevant -- use and cite only those that answer it, and still "
            "check the graph with the tools):\n"
            + json.dumps(result, ensure_ascii=False, default=str)})

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
            result = compact_tool_result(result)
            result["citations"] = [f"[{aid} \u2192 {locator}]"
                                   for aid, locator in sorted(citation_pairs([result]))]
            tool_results.append(result)
            entry = {"step": step + 1, "tool": name, "args": args,
                     "args_str": _arg_str(args),
                     "status": result.get("status"),
                     "template": _templates_for(name, args, result),
                     "summary": _summary(result)}
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

    return _finalize(question, answer, tool_results, log, trace,
                     relation_queries=relation_queries, stop_reason=stop_reason)


def _finalize(question: str, answer: str, tool_results: list[dict], log: list[dict],
              trace: list | None = None, relation_queries: int = 0,
              stop_reason: str = "completed") -> dict:
    """Status, citation grounding and not_found handling shared by both modes."""
    if trace is not None:
        trace.extend(log)
    proposed = re.match(r"\AStatus:\s*(\w+)", answer)
    state = answer_status(tool_results, proposed[1] if proposed else None)
    answer = re.sub(r"\AStatus:[^\n]*(?:\n|$)", "", answer)
    supported = citation_pairs(tool_results)
    answer = normalize_citations(answer, supported)
    missing = _missing_identifiers(question, tool_results)
    answer, facts = _grounded_answer(answer, supported)
    if proposed and proposed[1] == "conflict" and state != "conflict" and _cited_conflict(answer):
        state = "conflict"
    superseded = _supersession_lines(question, tool_results, supported)
    if superseded and state in ("applicable", "insufficient_context"):
        state = "superseded"
        answer = answer.rstrip("\n") + "\n\n## Conflicts / Superseded\n" + "\n".join(superseded)
    on_topic = _item_discussion(question, tool_results)
    kept, kept_facts = _grounded_answer(answer, {p for p in supported if p[1] in on_topic})
    if state == "not_found" and kept_facts and not missing:
        # Handling advice is kept only when it cites the conversation about
        # this very item (lines near a verbatim mention of its identifier).
        answer = kept.rstrip("\n") + "\n\n" + _not_found_answer(question, log, missing)
    elif missing or state == "not_found" or not facts:
        state = "not_found"
        answer = _not_found_answer(question, log, missing)
    answer = f"Status: {state}\n" + answer
    return {"question": question, "answer": answer, "trace": log,
            "tool_results": tool_results, "answer_status": state,
            "tool_calls": len(log), "relation_queries": relation_queries,
            "stop_reason": stop_reason}


def _summary(result: dict) -> dict:
    """What a call returned, in counts a reader can check (shown in the trace)."""
    out = {}
    for key in ("candidates", "assertions", "paths", "source_evidence", "conflicts", "handlers"):
        if result.get(key):
            out[key] = len(result[key])
    entity = result.get("entity") or {}
    if entity.get("name"):
        out["name"] = entity["name"]
    if result.get("superseded"):
        out["superseded"] = True
    return out


def _templates_for(name: str, args: dict, result: dict) -> list[str]:
    """Which server-side Cypher templates this call ran (for the eval log)."""
    if name == "describe_ontology":
        return ["(schema only, no Cypher)"]
    if name == "search_knowledge":
        if result.get("status") == "error":
            return ["(no query)"]
        keys = ["fulltext_search_kind" if args.get("kind") else "fulltext_search"]
        keys.append("id_prefix_kind" if args.get("kind") else "id_prefix")
        if result.get("source_evidence"):
            keys.append("source_search")
        return keys
    if name == "read_knowledge":
        return ["read_entity", "read_neighbors", "superseded_targets",
                "slot_claims", "claim_value_kinds", "evidence_by_id", "(relationship evidence join)"]
    if name == "query_relations":
        if result.get("status") == "error":
            return ["(rejected before query)"]
        return ["traverse", "superseded_targets", "slot_claims", "claim_value_kinds", "evidence_by_id"]
    return []
