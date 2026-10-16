"""Read-only HTTP adapter. Run with: python -m web.app."""
from __future__ import annotations

import os
import re
from functools import wraps

# A safe demo default, before db.py loads .env. Explicit configuration still wins.
os.environ.setdefault("GRAPH_NAME", "oncall_brain")

from flask import Flask, jsonify, request  # noqa: E402
from oncall_brain import agent, fast, tools  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
STATES = ("applicable", "conflict", "superseded", "insufficient_context", "not_found", "stale")


def available(fn):
    """Keep dependency exceptions (which can contain credentials) off the wire."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            return jsonify(status="error", error="Graph or model unavailable; check local configuration."), 503
    return wrapped


def graph_from(results):
    nodes, edges = {}, {}

    def node(value):
        nodes[value["id"]] = {**nodes.get(value["id"], {}), **value}

    def edge(source, target, predicate, aid, authority, directed=True):
        # An assertion may appear in reads from both ends; keep one edge.
        key = "|".join((str(aid), predicate, *sorted((source, target))))
        if key not in edges or directed:
            edges[key] = dict(id=key, source=source, target=target, predicate=predicate,
                              aid=aid, authority=authority, directed=directed)

    for result in results:
        if result.get("entity"):
            root = result["entity"]
            node({k: root[k] for k in ("id", "kind", "name") if k in root})
            nodes[root["id"]]["superseded"] = bool(result.get("superseded"))
            nodes[root["id"]]["root"] = True  # an entity this answer actually read
            for assertion in result.get("assertions", []):
                other = assertion["other"]
                node(other)
                source, target = root["id"], other["id"]
                if assertion["direction"] == "in":
                    source, target = target, source
                edge(source, target, assertion["predicate"], assertion.get("aid"), assertion.get("authority"))
        for path in result.get("paths", []):
            for value in path["nodes"]:
                node(value)
                if value["id"] in path.get("superseded_nodes", []):
                    nodes[value["id"]]["superseded"] = True
            for i, predicate in enumerate(path["edges"]):
                source, target = path["nodes"][i]["id"], path["nodes"][i + 1]["id"]
                if result.get("direction") == "in":
                    source, target = target, source
                edge(source, target, predicate, path["aids"][i], path["authority"][i],
                     directed=result.get("direction") != "both")
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def citations_from(answer, evidence):
    """Match source/locator, including model output whose aid itself contains ->."""
    citations = []
    canonical = r"^(?P<aid>.+?)\s+(?:->|→)\s+(?P<source>[^:\n]+):(?P<locator>\d+(?:-\d+)?)\s*$"
    compact = r"^(?P<aid>.*?)(?P<source>corpus/[^:\s]+):(?P<locator>\d+(?:-\d+)?)(?:\s.*)?$"
    for token in re.finditer(r"\[([^\]\n]+)\]", answer):
        match = re.match(canonical, token[1]) or re.match(compact, token[1])
        if not match:
            continue
        data = match.groupdict()
        data["source"] = data["source"].strip().strip("`")
        data["aid"] = data["aid"].rstrip(": ")
        item = next((e for e in evidence if e["source"] == data["source"]
                     and str(e["locator"]) == data["locator"]), None)
        citations.append({**data, "text": token[0], "evidence_id": item["id"] if item else None})
    return citations


def answer_status(answer, results, evidence, incomplete=False):
    # Respect a structured/explicit model status if provided in prose.
    match = re.search(r"(?:answer[_ ]?status|status|状态)\s*[:：]\s*[`*]*(" + "|".join(STATES) + r")\b", answer, re.I)
    if match:
        return match.group(1).lower(), "answer_label"
    if incomplete:
        return "insufficient_context", "incomplete_readback"
    if any(r.get("conflicts") for r in results):
        return "conflict", "graph_summary"
    if any(r.get("superseded") for r in results):
        return "superseded", "graph_summary"
    if any(r.get("applicability") == "insufficient_context" or any(
            a.get("applicability") == "insufficient_context" for a in r.get("assertions", [])) for r in results):
        return "insufficient_context", "graph_summary"
    return ("applicable" if evidence else "not_found"), "graph_summary"


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/examples")
@available
def examples():
    """The 17 gold-card questions, so the UI never keeps its own copy."""
    from eval.run_gold import parse_gold
    return jsonify([{"qid": q["qid"], "question": q["question"],
                     "expected_status": q["expected_status"]} for q in parse_gold()])


@app.get("/health")
def health_page():
    return app.send_static_file("health.html")


@app.get("/api/health")
@available
def health():
    """Knowledge-base health check: read-only counts, single points, conflicts."""
    from oncall_brain.health import kb_health
    return jsonify(kb_health())


@app.get("/api/ontology")
@available
def ontology():
    return jsonify(tools.describe_ontology())


@app.get('/demo')
def demo_page():
    return app.send_static_file('freshness.html')


@app.post('/api/demo/notice')
@available
def demo_notice():
    from uuid import uuid4
    from oncall_brain import freshness
    graph = os.environ.get('DEMO_GRAPH', '')
    try:
        freshness._demo(graph)
    except ValueError:
        return jsonify(status='error', error='DEMO_GRAPH must end in _demo.'), 403
    payload = request.get_json(silent=True)
    fields = ('name', 'text', 'supersedes_id', 'effective')
    if not isinstance(payload, dict) or any(not isinstance(payload.get(k), str) or
                                           not payload[k].strip() for k in fields):
        return jsonify(status='error', error='name, text, supersedes_id, effective are required.'), 400
    try:
        return jsonify(freshness.add_notice(graph, f'Notice:Demo_{uuid4().hex}',
                                           *(payload[k] for k in fields)))
    except ValueError as exc:
        return jsonify(status='error', error=str(exc)), 400


@app.post('/api/demo/reset')
@available
def demo_reset():
    from oncall_brain import freshness
    graph = os.environ.get('DEMO_GRAPH', '')
    try:
        freshness._demo(graph)
    except ValueError:
        return jsonify(status='error', error='DEMO_GRAPH must end in _demo.'), 403
    return jsonify(freshness.reset_demo(graph))


@app.get("/api/search")
@available
def search():
    query = request.args.get("q", "").strip()
    if not query or len(query) > 500:
        return jsonify(status="error", error="q must contain 1–500 characters."), 400
    return jsonify(tools.search_knowledge(query))


@app.get("/api/node/<path:node_id>")
@available
def read_node(node_id):
    result = tools.read_knowledge(node_id)
    return jsonify(result), 404 if result["status"] == "not_found" else 200


@app.get("/api/neighbors/<path:node_id>")
@available
def neighbors(node_id):
    root = tools.read_knowledge(node_id)
    if root["status"] == "not_found":
        return jsonify(root), 404
    result = tools.query_relations(node_id, list(tools.describe_ontology()["predicates"]),
                                   direction="both", max_hops=1, limit=50)
    # Read assertions preserve real edge direction; undirected traversal alone cannot.
    result["graph"] = graph_from([result, root])
    return jsonify(result)


@app.post("/api/ask")
@available
def ask():
    payload = request.get_json(silent=True)
    question = payload.get("question") if isinstance(payload, dict) else None
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
        return jsonify(status="error", error="question must contain 1–4000 characters."), 400
    mode = payload.get("mode", "fast")
    lang = payload.get("lang") if payload.get("lang") in ("zh", "en") else None
    trace = []
    if mode == "deep":
        result = dict(agent.ask(question.strip(), trace=trace, lang=lang))
    else:
        result = dict(fast.ask_fast(question.strip(), trace=trace, lang=lang))
    result.setdefault("trace", trace)
    result["mode"] = "deep" if mode == "deep" else "fast"
    # Evidence and graph come from this question's own tool results (graph reads
    # and source lines); replaying every call again only added seconds.
    readbacks, warnings, evidence = [], [], {}
    for data in result.get("tool_results", []):
        readbacks.append(data)
        for item in data.get("evidence", []) + data.get("source_evidence", []):
            if isinstance(item, dict) and "id" in item:
                evidence[item["id"]] = item
    result["evidence"] = list(evidence.values())
    result["citations"] = citations_from(result.get("answer", ""), result["evidence"])
    unmatched = sum(c["evidence_id"] is None for c in result["citations"])
    if unmatched:
        warnings.append(f"{unmatched} citation(s) have no matching source/locator in the returned evidence.")
    result["graph"] = graph_from(readbacks)
    result["status"], result["status_source"] = answer_status(
        result.get("answer", ""), readbacks, evidence, bool(warnings))
    if unmatched:
        result["status"], result["status_source"] = "insufficient_context", "citation_validation"
    result["evidence_origin"] = "tool_results"
    result["warnings"] = warnings
    # Owner vs who has actually handled the module (module_expertise), for the UI card.
    result["expertise"] = [{k: data.get(k) for k in ("start_id", "owners", "handlers", "evidence")}
                           for data in readbacks if data.get("handlers")]
    result.pop("tool_results", None)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("WEB_PORT", "8000")), debug=False)
