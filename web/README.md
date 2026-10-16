# OnCall Brain Web

A single-page, read-only Graph Hacks demo. No frontend build is required.
Run these commands from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:GRAPH_NAME = 'oncall_brain'
python -m web.app
```

Open <http://localhost:8000>. Flask binds to loopback only; debug mode is off.
Set `WEB_PORT` to change the port. Keep the existing `.env` local: the agent
loads its LLM configuration from it. FalkorDB must already be running on the
configured local port. This app never ingests, initializes, or wipes graphs.
The default graph is `oncall_brain`; the explicit environment variable
takes precedence. The pinned Cytoscape.js 3.30.4 CDN requires internet access.

Click Q1, Q4, or Q15 to submit the exact question from `docs/02-答案卡.md`.
Click an answer citation to inspect its original graph-provided excerpt and
authority. Chat evidence uses amber; formal evidence uses green. Click graph
nodes to load one-hop neighbors. Search and Ontology also work without an LLM.

## HTTP API

| Method | Route | Result |
| --- | --- | --- |
| POST | `/api/ask` | JSON `{ "question": "..." }`; answer, original trace, evidence, status and graph |
| GET | `/api/node/{id}` | `read_knowledge` entity, assertions and evidence; 404 if absent |
| GET | `/api/search?q=Pipeline` | Search candidates or `not_found` |
| GET | `/api/ontology` | Entity kinds, predicates, authority levels and budgets |
| GET | `/api/neighbors/{id}` | One-hop `query_relations` paths, evidence and `graph.nodes/edges` |

The agent's trace does not retain tool result bodies. `/api/ask` replays its
read-only tool calls to enrich evidence and graph data after the single agent
invocation (`evidence_origin: read_only_trace_replay`). These reads are outside
the agent's tool budget and may reflect a later snapshot if the graph changes.
They do not appear as extra model tool calls. Readback failures are reported in
`warnings`. Citation matching uses exact graph source/locator; unmatched model
citations explicitly show that their excerpt is unavailable. No file path from
an answer is opened on disk.

The adapter also recognizes compact model citations whose assertion IDs contain
arrows. `citations` records each parsed source/locator and matched evidence ID.
If a citation cannot be verified, status becomes `insufficient_context` with
`status_source: citation_validation`; missing evidence is never fabricated.

`status_source` distinguishes explicit model status labels from `graph_summary`.
The existing agent produces prose, not a structured answer status. The fallback
summarizes retrieved conflicts, superseded rules and missing conditions; it is
not an independent semantic assessment of the prose answer. Inspect the answer
and its evidence, particularly for role-distinction questions such as Q15.
Bidirectional traversal edges have no arrow unless a node read verifies their
direction. The trace's Cypher template names are passed through from the agent.

## Tests

```powershell
$env:GRAPH_NAME = 'oncall_brain'
python -m unittest discover -s tests -p test_web.py -v
```

Tests use Flask's test client and real read-only graph queries for ontology,
search, node and neighbors. The ask integration test substitutes only the LLM
answer and replays real graph reads. It does not spend model calls. Ontology
and page tests need no graph; the ones that read real rows skip when the
configured graph is missing or empty.

`tests/browser_smoke.py` is optional Playwright QA. It takes `--recording` (a
saved `/api/ask` JSON response) and `--output` (a writable QA directory). It
intercepts only ask requests with that recording, exercises live node/neighbor
and ontology APIs, and saves desktop/mobile screenshots without another LLM call.
