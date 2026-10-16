# OnCall Brain

> Ask the company knowledge graph who owns what, which rule is current, and what to do next — every claim traced back to a file and line.

A support organisation spread across three vendors, four teams and twelve regional
territories has its rules scattered over operations docs, chat logs and ticket
exports. OnCall Brain fuses them into one FalkorDB knowledge graph and answers
questions through four read-only tools, citing the assertion id and its evidence
location for every claim. When the material has no answer it says so instead of
guessing.

All data describes **AetherPulse**, a fictional company. The corpus is synthetic;
no real organisation, person or contact appears anywhere in it.

**Track:** Graph Hacks — **Company Brain** (docs, tickets and chat fused into one
graph; who owns what vs. who curates what; every answer traceable to its source;
superseded rules kept out of current answers).

## Install

Requires Python 3.12, a FalkorDB instance (e.g. `docker run -p 6379:6379 falkordb/falkordb`)
and any OpenAI-compatible chat endpoint that supports tool calling.

```bash
pip install -r requirements.txt

# .env (gitignored) — never commit real keys
FALKOR_HOST=127.0.0.1
FALKOR_PORT=6379
GRAPH_NAME=oncall_brain
LLM_BASE_URL=https://<your-openai-compatible-endpoint>/v1
LLM_MODEL=<a tool-calling model, e.g. gemini-3.8-flash-high>
LLM_API_KEY=<your key>
# optional: LLM_JUDGE_MODEL=<a different model for eval judging>
```

Build and query:

```bash
python cli.py ingest            # rebuild the graph from corpus/
python cli.py approve           # approve candidate assertions (logged)
python cli.py stats             # graph size + source distribution
python cli.py ask "Where can I find our module knowledge base for Pipeline?"
python cli.py mcp               # serve the same tools over MCP (stdio)

python eval/run_gold.py         # 17 gold-card questions -> eval/results/latest.md
python -m web.app               # web UI on http://127.0.0.1:8000
```

The web UI shows three panes: the cited answer (click any citation to read the
source line), the subgraph behind it (click a node to expand its neighbours), and
the real tool-call trace with the Cypher template names each call executed.

## Graph model

Business nodes all carry `:Entity`; `Evidence` nodes deliberately do not, so they
never appear in business traversals.

| Label | Example | Notes |
|---|---|---|
| Person / Team / Vendor / Role | Colin Marsh, Operations Engineering Team (L2) | Role ≠ Person |
| Module | MOD-01 … MOD-29 | |
| Territory | Northland Metro … | 12 territories |
| Resource | Knowledge Hub - MOD-01, Sentinel Desk | `rtype`: kb / portal / channel / form / doc |
| Procedure | Escalate_to_Tier2, Weekly_Report_Submission | |
| WorkStage | Day1_Onboarding, Daily_Response | |
| Rule / Notice | Rule:Intake_Claim_SLA_15_Min | Rules may carry a `condition` |
| Ticket | TCK-1001 … | |

### Controlled predicates

Unregistered predicates are rejected at both ingest and query time. Domain and
range are validated per assertion.

| Predicate | Direction | Single-valued | Semantics / no-derivation rule |
|---|---|---|---|
| MEMBER_OF | Person → Team | no | membership; `primary` marks the main assignment |
| EMPLOYED_BY | Person → Vendor | yes | vendor affiliation |
| HOLDS_ROLE | Person → Role | no | time-boxed; expired on handover |
| TECH_LEAD_OF | Person → Module | no | module Tier 2 owner — **the escalation target** |
| HAS_RESOURCE | Module → Resource | no | knowledge base / portal / channel |
| MAINTAINED_BY | Resource → Person | no | proves document curation **only**; never an escalation target |
| COVERS_TERRITORY | Person → Territory | no | field coverage |
| HAS_STEP | Procedure → WorkStage | no | |
| PRECEDES / PARALLEL_WITH | WorkStage → WorkStage | no | ordering |
| PERFORMED_BY | Procedure → Role\|Team\|Person | no | who executes |
| RECEIVED_BY | Procedure → Role\|Team\|Person | no | who receives; needs direct evidence |
| USES_RESOURCE | Procedure\|WorkStage\|Module\|Role\|Rule → Resource | no | entry point / form |
| REQUIRES_FIELD | Procedure → Resource | no | mandatory form field |
| GOVERNED_BY | Procedure\|WorkStage\|Resource\|Rule → Rule\|Notice\|Resource | no | applicable rule |
| APPLIES_TO | Rule\|Notice → Module\|WorkStage\|Territory | no | scope |
| SUPERSEDES | Notice\|Rule → Rule | no | old rule leaves current answers |
| ABOUT / HANDLED_BY | Ticket → Module / Ticket → Person | no | ticket facts |

Responsibility predicates never imply each other: `MAINTAINED_BY` ≠ `TECH_LEAD_OF`
≠ `RECEIVED_BY` ≠ `MEMBER_OF`. A duty without direct evidence is reported as a gap.

Each assertion carries `aid`, `authority` (`formal_rule` > `practice` >
`observation`), `valid_from` / `valid_to`, `state`, and its evidence ids.

### Visibility

Every hop is filtered server-side:

```
r.state = 'approved'
AND r.valid_from <= $now
AND (r.valid_to IS NULL OR r.valid_to > $now)
```

An entity targeted by a visible `SUPERSEDES` edge is flagged superseded and held
out of current answers, but surfaced together with its replacement and effective
date.

## Cypher templates

The model never writes Cypher — it selects predicates, direction and hop count,
and the server substitutes them into these templates (all in
`oncall_brain/tools.py:TEMPLATES`):

| Template | Purpose |
|---|---|
| `fulltext_search` / `fulltext_search_kind` | entity lookup by name via the fulltext index, optionally filtered by kind |
| `id_prefix` / `id_prefix_kind` | substring fallback when the fulltext index misses |
| `read_entity` | properties of one entity |
| `read_neighbors` | all visible assertions touching an entity (both directions) |
| `superseded_targets` | every entity currently superseded, with replacement and date |
| `traverse` | variable-length path expansion with per-hop visibility filtering via `all(rel IN relationships(p) WHERE ...)` |
| `evidence_by_id` | resolve evidence records to source/locator/excerpt/authority |
| `source_search` | verbatim lookup over `Evidence` lines, for identifiers no entity name carries |
| `slot_claims` / `claim_value_kinds` | competing values for one semantic slot, for conflict detection |

## Tools

| Tool | Input | Output |
|---|---|---|
| `describe_ontology` | — | entity kinds, predicate semantics, authority levels, behaviour rules, budget |
| `search_knowledge` | `query`, `kind?`, `limit?` | candidate ids; `not_found` when nothing matches |
| `read_knowledge` | `id`, `context?` | properties + all visible assertions with aid, authority, evidence, applicability |
| `query_relations` | `start_id`, `predicates[]`, `direction?`, `max_hops≤3`, `context?` | paths, conflicts, evidence, truncation flag |

Budget: ≤8 tool calls and ≤6 relation queries per question. IDs may only come
from tool results. The same four tools are exposed over MCP stdio
(`python cli.py mcp`) and used by the CLI and the agent.

Before the tool loop the agent also receives the source lines that share the most
informative (IDF-weighted) words with the question, so official passages the model
would not think to search for are in view; this lookup does not count against the
budget. `read_knowledge` returns the lines that follow each evidence line in the
same thread, where enumerations and penalties usually sit.

Answers follow a fixed shape: **Next step → Basis (each claim citing
`[aid → file:line]`) → Actual common practice → Conflicts / Superseded → Gaps**.

## Ingest

```
corpus/ → slices → LLM-proposes assertions (registered predicates only)
       → validate (predicate / domain / range / evidence line / entity id)
       → state=candidate → approve → visible
```

Structured sources (`org/*`, `tickets/*`, `docs/08-*`) are parsed
deterministically without an LLM. Chat-derived assertions default to
`observation`; only ones announced as policy become `practice`. Chat assertions
never auto-promote to `formal_rule`. Every assertion cites `file:line` and the
line must really contain the quoted text or it lands in `ingest/rejected.jsonl`.
LLM slice results are cached by content hash, so reruns make no repeat calls.

## Evaluation

`python eval/run_gold.py` runs the 17 gold-card questions in `docs/02-答案卡.md`
(card text in Chinese, questions in English)
against the live graph and writes `eval/results/latest.md`. Each entry records the
real tool sequence with arguments, the Cypher templates executed, the verbatim
answer, the citations it emitted, and PASS/FAIL against that card's own expected
state, point list and forbidden errors.

A committed snapshot of the latest full run is in `eval/snapshot/latest.md`;
regenerate it with the command above. Judging is done by an LLM against each
card, so single runs vary by a question or two; set `LLM_JUDGE_MODEL` to judge
with a model other than the one answering.

## FalkorDB notes

Two behaviours of the FalkorDB build this was developed against shaped the code:

- Creating an edge *from* a bound relationship variable fails
  (`The alias 'r' was specified for both a node and a relationship`), regardless
  of alias name, `MERGE` vs `CREATE`, or re-matching the relationship. Evidence is
  therefore stored as the relationship's `evidence` property rather than as
  `(rel)-[:EVIDENCE]->(e)`.
- A `MATCH`-based `MERGE` writes nothing when an endpoint does not exist, failing
  silently. Each edge now merges its own endpoints first, so payload ordering
  cannot drop assertions.

## Disclosure

All company data is fictional. AI coding assistants (Claude Code and OpenAI Codex)
were used to write much of this code under the author's design and review; every
reported metric comes from running the commands above rather than from an
assistant's claims.
