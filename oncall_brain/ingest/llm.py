"""LLM-based extraction from free-text corpus sources.

Pipeline per design doc §5:
  slice (doc by section / chat by topic window) -> LLM proposes candidate
  assertions (registered predicates only) -> validation (predicate registered,
  domain/range, evidence line exists, quote really on that line) -> ID
  canonicalisation -> graph write as state=candidate -> approve step.

Extraction is generic. The prompt is assembled from the registered predicate
registry, the slice text and the entity vocabulary derived from the structured
corpus sources (roster, module registry, territory list). It contains no
question numbers and no hand-mapped facts, so nothing here is tuned to a
particular evaluation question; a slice that states nothing gives nothing.

Chat-derived assertions default to authority=observation. Rules/notices
announced in a slice carry an effective date and, when they replace an older
rule, a SUPERSEDES edge.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .. import schema as S
from . import evidence as EV
from .labeler import label_of

CACHE_DIR = Path(__file__).resolve().parents[2] / "ingest" / "cache"

# Line slice sizes. A chat window keeps a whole topic exchange in view; a doc
# section is the natural unit of a formal rule.
CHAT_WINDOW = 25
MAX_SLICE_LINES = 60


# --------------------------------------------------------------------------
# LLM client
# --------------------------------------------------------------------------
def _llm():
    import openai
    from ..db import ROOT

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return openai.OpenAI(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        timeout=120, max_retries=1,
    )


def _model() -> str:
    return os.environ.get("LLM_MODEL", "gemini-3.8-flash-high")


# --------------------------------------------------------------------------
# Entity vocabulary: IDs grounded in the structured corpus sources.
# --------------------------------------------------------------------------
_LABEL_PREFIX = {
    "Procedure": "Procedure", "Rule": "Rule", "Notice": "Notice",
    "Stage": "WorkStage", "Resource": "Resource", "Role": "Role",
    "Person": "Person", "Team": "Team", "Module": "Module",
}


def vocabulary() -> dict[str, list[str]]:
    """Known entity ids by label, read from the deterministic corpus sources.

    Giving the extractor the real ids up front is what keeps "Knowledge Hub
    MOD-01" and "the KB for MOD-01" from becoming two entities.
    """
    from .. import schema as SCHEMA
    from . import deterministic as D

    people = sorted(D.roster_names_for_vocab())
    modules = sorted(D.module_ids())
    return {
        "Person": people,
        "Module": modules,
        "Territory": list(SCHEMA.TERRITORIES),
        "Team": [t["id"] for t in D.TEAMS],
        "Vendor": [v["name"] for v in D.VENDORS],
        "Resource": sorted(D.resource_ids()),
    }


def _vocab_block(vocab: dict[str, list[str]]) -> str:
    lines = []
    for label, ids in vocab.items():
        if not ids:
            continue
        shown = ids if len(ids) <= 90 else ids[:90] + ["..."]
        lines.append(f"{label}: " + ", ".join(shown))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Slicing
# --------------------------------------------------------------------------
def slice_chat(source: str, window: int = CHAT_WINDOW):
    """Yield (chunk, line_offset) topic windows for one chat file."""
    ls = EV.lines(source)
    for start in range(0, len(ls), window):
        part = ls[start:start + window]
        chunk = "\n".join(f"[{start + i + 1}] {ln}" for i, ln in enumerate(part))
        yield chunk, start


def slice_doc(source: str, max_lines: int = MAX_SLICE_LINES):
    """Yield (chunk, line_offset) sections for one markdown document.

    A section starts at any heading. Long sections are split further so the
    extractor still sees a manageable amount of text at once.
    """
    ls = EV.lines(source)
    starts = [i for i, ln in enumerate(ls) if re.match(r"^#{1,6}\s", ln)]
    if not starts:
        starts = [0]
    bounds = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(ls)
        bounds.append((s, e))
    # merge tiny leading sections (title block) into the first real one
    merged: list[tuple[int, int]] = []
    for s, e in bounds:
        if merged and s - merged[-1][1] == 0 and merged[-1][1] - merged[-1][0] < 3:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    for s, e in merged:
        for sub in range(s, e, max_lines):
            hi = min(sub + max_lines, e)
            chunk = "\n".join(f"[{i + 1}] {ls[i]}" for i in range(sub, hi))
            yield chunk, sub


def slice_source(source: str):
    """Dispatch on the corpus directory: docs by section, chats by window."""
    if source.startswith("corpus/docs/"):
        yield from slice_doc(source)
    else:
        yield from slice_chat(source)


def sources() -> list[str]:
    """Every free-text source that goes through LLM extraction."""
    from . import evidence as E

    out = []
    for sub in ("docs", "chats"):
        d = E.ROOT / "corpus" / sub
        for f in sorted(d.glob("*.md")):
            out.append(f"corpus/{sub}/{f.name}")
    return out


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
_AUTHORITY_RULE = {
    "doc": ('"formal_rule" for statements the document lays down as policy, '
            '"practice" for a documented convention that is not a rule; never '
            '"observation"'),
    "chat": ('"observation" for anything a single person says; "practice" only '
             'when the message announces a decision/notice that others confirm '
             'or that is dated and formal; never "formal_rule"'),
}


def llm_extract_slice(chunk: str, source: str, kind: str = "chat",
                      vocab: dict[str, list[str]] | None = None) -> list[dict]:
    """Ask the LLM to propose assertions for one slice. Cached by content hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    vocab = vocab or vocabulary()
    key_src = f"{source}\n{kind}\n" + "\n".join(
        f"{k}:{','.join(v)}" for k, v in sorted(vocab.items())) + "\n" + chunk
    h = hashlib.sha256(key_src.encode()).hexdigest()[:16]
    cf = CACHE_DIR / f"{h}.json"
    if cf.exists():
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass  # corrupt cache entry: re-extract
    client = _llm()
    preds = "\n".join(
        f"- {p}: {d} -> {r}. {m}" for p, (d, r, _, m) in S.PREDICATES.items())
    prompt = f"""You are extracting knowledge-graph assertions from one slice of an
enterprise operations corpus. The company is AetherPulse (fictional).
Source file: {source} ({"a formal document" if kind == "doc" else "an operations chat log"}).

REGISTERED PREDICATES (use only these; name: domain -> range):
{preds}

ENTITY IDS already known to the graph (reuse these exact strings; do not invent
a near-duplicate of one of them):
{_vocab_block(vocab)}

For entities that are NOT in that list, mint a stable id:
  Procedure:<Snake_Case_Name>   Rule:<Snake_Case_Name>
  Notice:<Type>_<YYYYMMDD>_<Snake_Case_Name>   Stage:<Snake_Case_Name>
  Resource:<Name>   Role:<Name>
A resource that is a channel, portal, form or knowledge-base page is a Resource.

RULES:
1. Only assert what this slice actually states. No inference from general
   knowledge, no filling in of things the slice does not say.
2. Every assertion MUST cite evidence_line (the line number printed at the start
   of the line inside [ ]) and evidence_text (that line verbatim, complete).
3. authority MUST be {_AUTHORITY_RULE[kind]}.
4. valid_from: YYYY-MM-DD the assertion became true (a document's own date, the
   chat date, or a date stated in the text). valid_to only if the text says it
   ends.
5. If the slice announces a decision/notice that REPLACES an older rule, also
   emit a SUPERSEDES edge (Notice or Rule -> the old Rule) with
   valid_from = the effective date, and set valid_to on nothing else.
6. If the text states a rule that only holds under a condition (for example a
   severity level or a module), put it in props as
   "condition": "severity=P3" (or "module=MOD-01").
7. Do not emit the same assertion twice.

Return STRICT JSON only:
{{"assertions": [{{"subject": "...", "predicate": "...", "object": "...",
"evidence_line": <int>, "evidence_text": "<the whole line>",
"authority": "formal_rule|practice|observation", "valid_from": "YYYY-MM-DD",
"valid_to": null, "props": {{"condition": "severity=P3"}}}}]}}

Slice (line numbers in [ ] at line start, matching evidence_line):
{chunk}"""
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, response_format={"type": "json_object"},
                max_tokens=4000)
            data = json.loads(r.choices[0].message.content or "{}")
            out = data.get("assertions") or []
            cf.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return out
        except Exception as e:  # noqa: BLE001 - retried, then reported as a count
            if attempt == 2:
                raise RuntimeError(f"LLM extraction failed: {type(e).__name__}") from None
    return []


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _is_reversed(a: dict) -> bool:
    """True when swapping subject/object would make the assertion valid.

    The extractor reliably identifies the two entities involved but regularly
    emits the edge backwards ("MOD-01 -TECH_LEAD_OF-> Colin Marsh"). Rejecting
    those loses real facts; flipping them when exactly one orientation is
    well-typed keeps them and is unambiguous, because the schema domain/range
    are disjoint for these predicates.
    """
    pre = a.get("predicate")
    if not pre or not S.is_registered(pre):
        return False
    s, o = str(a.get("subject")), str(a.get("object"))
    sl, ol = label_of(s), label_of(o)
    if S.validate_predicate(pre, sl, ol) is None:
        return False  # already well-typed; nothing to flip
    return S.validate_predicate(pre, ol, sl) is None


def repair_direction(a: dict) -> dict:
    """Flip a backwards assertion in place; returns it either way."""
    if _is_reversed(a):
        a["subject"], a["object"] = a["object"], a["subject"]
        a["reversed_from_llm"] = True
    return a


def validate(a: dict) -> str | None:
    """Validate one candidate assertion. Returns a reject reason or None."""
    pre = a.get("predicate")
    if not pre or not S.is_registered(pre):
        return f"unregistered predicate '{pre}'"
    sl, ol = label_of(str(a.get("subject"))), label_of(str(a.get("object")))
    reason = S.validate_predicate(pre, sl, ol)
    if reason:
        return reason
    ls = EV.lines(a["source"])
    if not (1 <= int(a["line"]) <= len(ls)):
        return f"evidence line {a['line']} out of range in {a['source']}"
    return None


def quote_matches(a: dict) -> bool:
    """The quoted evidence_text must really appear on (or beside) the line."""
    q = (a.get("evidence_text") or "").strip()
    if not q:
        return True  # nothing quoted; the line number alone is the evidence
    ls = EV.lines(a["source"])
    i = int(a["line"])
    # Slices number each line ("[28] ..."); models often quote that prefix too.
    q = re.sub(r"^\[\d+\]\s*", "", q.strip().strip('"').strip("'"))
    norm = re.sub(r"\s+", " ", q)
    window = " ".join(ls[max(0, i - 2):i + 1])
    return norm[: min(60, len(norm))] in re.sub(r"\s+", " ", window)


# --------------------------------------------------------------------------
# ID canonicalisation (entity alignment by id / alias, design doc §5)
# --------------------------------------------------------------------------
_PREFIX_RE = re.compile(r"^(Procedure|Rule|Notice|Stage|WorkStage|Resource|Role|Person|Team|Module|Vendor|Territory|Ticket):")


def _split_id(nid: str) -> tuple[str, str]:
    m = _PREFIX_RE.match(nid)
    if m:
        return m.group(1), nid[m.end():]
    return "", nid


def _norm_base(base: str) -> str:
    """Collapse cosmetic differences: case, separators, KB/MOD spacing."""
    b = base.lower()
    b = b.replace("_", " ").replace("-", " ").replace("/", " ")
    b = re.sub(r"\bkb\b", "knowledge hub", b)
    b = re.sub(r"\bmod\s*(\d+)\b", r"mod \1", b)
    b = re.sub(r"[^a-z0-9 ]+", " ", b)
    return re.sub(r"\s+", " ", b).strip()


def canonicalise(edges: list[dict], known: set[str]) -> tuple[list[dict], dict[str, str]]:
    """Map cosmetic id variants onto one canonical id per cluster.

    Two ids land in the same cluster only when their label prefix and their
    normalised base are identical, so distinct entities are never merged; this
    only undoes spelling differences ("Knowledge Hub MOD-01" vs
    "Knowledge Hub - MOD-01"). Within a cluster a known deterministic id wins,
    otherwise the most frequent spelling (shortest on a tie).
    """
    clusters: dict[tuple[str, str], list[str]] = {}
    ids = list(sorted(known)) + [str(e[k]) for e in edges for k in ("subject", "object")]
    for nid in ids:
        prefix, base = _split_id(nid)
        kind = {"Stage": "WorkStage"}.get(prefix, prefix) if prefix else label_of(nid)
        clusters.setdefault((kind, _norm_base(base)), []).append(nid)

    mapping: dict[str, str] = {}
    for (prefix, _), variants in clusters.items():
        counts: dict[str, int] = {}
        for v in variants:
            counts[v] = counts.get(v, 0) + 1
        known_here = [v for v in counts if v in known]
        if known_here:
            canon = sorted(known_here, key=lambda v: (-counts[v], len(v), v))[0]
        else:
            canon = sorted(counts, key=lambda v: (-counts[v], len(v), v))[0]
        for v in counts:
            mapping[v] = canon
    for e in edges:
        e["subject"] = mapping.get(str(e["subject"]), str(e["subject"]))
        e["object"] = mapping.get(str(e["object"]), str(e["object"]))
    return edges, {k: v for k, v in mapping.items() if k != v}
