"""FalkorDB access layer + node/edge write helpers for the oncall_brain graph."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

HOST = os.environ.get("FALKOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("FALKOR_PORT", "16379"))
GRAPH_NAME = os.environ.get("GRAPH_NAME", "oncall_brain")

_g = None


def g():
    global _g
    if _g is None:
        _g = FalkorDB(host=HOST, port=PORT).select_graph(GRAPH_NAME)
    return _g


def wipe():
    db = FalkorDB(host=HOST, port=PORT)
    if GRAPH_NAME.encode() in db.list_graphs() or GRAPH_NAME in [x.decode() if isinstance(x, bytes) else x for x in db.list_graphs()]:
        db.delete_graph(GRAPH_NAME)
    globals()["_g"] = None


def ensure_indexes():
    for q in (
        "CREATE FULLTEXT INDEX FOR (n:Entity) ON (n.name)",
        "CREATE FULLTEXT INDEX FOR (n:Entity) ON (n.id)",
    ):
        try:
            g().query(q)
        except Exception:
            pass  # already exists


def node(label: str, nid: str, **props) -> str:
    """MERGE a node by id, set label + properties. Returns the id."""
    props = {k: v for k, v in props.items() if v is not None}
    pq = ", ".join(f"n.{k} = ${k}" for k in props)
    q = (f"MERGE (n:Entity:{label} {{id: $id}}) "
         + (f"SET {pq} " if pq else "")
         + "RETURN n.id")
    g().query(q, {"id": nid, **props})
    return nid


def evidence_node(eid: str, source: str, locator: str, excerpt: str, authority: str) -> str:
    """MERGE an Evidence node (no :Entity label; not traversed by business queries)."""
    q = ("MERGE (e:Evidence {id: $id}) "
         "SET e.source = $source, e.locator = $locator, "
         "e.excerpt = $excerpt, e.authority = $authority RETURN e.id")
    g().query(q, {"id": eid, "source": source, "locator": locator,
                  "excerpt": excerpt, "authority": authority})
    return eid


def edge(subject: str, predicate: str, obj: str, *,
         aid: str, authority: str, evidence_ids: list[str],
         state: str = "candidate", valid_from: str = "2026-01-01",
         valid_to: str | None = None, **props) -> None:
    """MERGE a relationship by aid and attach its properties + evidence edges."""
    props = {"aid": aid, "authority": authority, "state": state,
             "valid_from": valid_from, "valid_to": valid_to,
             "evidence": evidence_ids, **props}
    pq = ", ".join(f"r.{k} = ${k}" for k in props)
    q = (f"MATCH (a:Entity {{id: $s}}), (b:Entity {{id: $o}}) "
         f"MERGE (a)-[r:{predicate} {{aid: $aid}}]->(b) "
         f"SET {pq} "
         "WITH r UNWIND $ev AS eid MATCH (e:Evidence {id: eid}) "
         "MERGE (r)-[:EVIDENCE]->(e)")
    g().query(q, {"s": subject, "o": obj, "aid": aid, "ev": evidence_ids, **props})


def batch_write(payload: list[dict]) -> None:
    """Batch of node/evidence/edge dicts, each tagged by 'op'."""
    for item in payload:
        op = item.pop("op")
        if op == "node":
            node(item["label"], item["id"], **item.get("props", {}))
        elif op == "evidence":
            evidence_node(item["id"], item["source"], item["locator"],
                          item["excerpt"], item["authority"])
        elif op == "edge":
            edge(item["subject"], item["predicate"], item["object"],
                 aid=item["aid"], authority=item["authority"],
                 evidence_ids=item["evidence"], state=item.get("state", "candidate"),
                 valid_from=item.get("valid_from", "2026-01-01"),
                 valid_to=item.get("valid_to"), **item.get("props", {}))
        else:
            raise ValueError(f"unknown op {op}")


def approve_all() -> int:
    rows = g().query(
        "MATCH ()-[r]->() WHERE r.state = 'candidate' "
        "SET r.state = 'approved' RETURN count(r)").result_set
    return int(rows[0][0])


def approve_predicate(predicate: str) -> int:
    rows = g().query(
        f"MATCH ()-[r:{predicate}]->() WHERE r.state = 'candidate' "
        "SET r.state = 'approved' RETURN count(r)").result_set
    return int(rows[0][0])


def stats() -> dict:
    def scalar(q, p=None):
        return int(g().query(q, p or {}).result_set[0][0])
    nodes = scalar("MATCH (n:Entity) RETURN count(n)")
    evs = scalar("MATCH (e:Evidence) RETURN count(e)")
    edges = scalar("MATCH ()-[r]->() WHERE type(r) <> 'EVIDENCE' RETURN count(r)")
    per_pred = {}
    for row in g().query(
            "MATCH ()-[r]->() WHERE type(r) <> 'EVIDENCE' "
            "RETURN type(r), count(r) ORDER BY type(r)").result_set:
        per_pred[row[0]] = int(row[1])
    per_source = {}
    for row in g().query(
            "MATCH (e:Evidence) RETURN e.source, count(e) ORDER BY e.source").result_set:
        per_source[row[0]] = int(row[1])
    return {"nodes": nodes, "evidence": evs, "edges": edges,
            "per_predicate": per_pred, "per_source": per_source}
