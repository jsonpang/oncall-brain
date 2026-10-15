"""OnCall Brain CLI.

    python cli.py ingest [--no-llm]   rebuild the graph from corpus/
    python cli.py approve             approve candidate assertions (logged)
    python cli.py ask "question"      answer with tools + evidence
    python cli.py stats               graph size and source distribution
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from oncall_brain import agent, db, tools  # noqa: E402

APPROVE_LOG = ROOT / "ingest" / "approved.log"


def cmd_ingest(args) -> int:
    from oncall_brain.ingest.run import main as run_ingest

    st = run_ingest(use_llm=not args.no_llm)
    print(json.dumps(st, indent=2, ensure_ascii=False))
    return 0


def cmd_approve(args) -> int:
    n = db.approve_all()
    APPROVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(APPROVE_LOG, "a", encoding="utf-8") as f:
        f.write(f"{stamp}\tapproved\t{n}\n")
    print(f"approved {n} candidate assertions -> {APPROVE_LOG.relative_to(ROOT)}")
    return 0


def cmd_ask(args) -> int:
    trace: list[dict] = []
    res = agent.ask(args.question, trace=trace)
    print(res["answer"])
    print()
    print("--- tool calls ---")
    for t in trace:
        print(f"{t['step']}. {t['tool']}({t['args_str']}) -> {t['status']}")
        print(f"   templates: {', '.join(t['template'])}")
    print(f"({res['tool_calls']} calls, {res['relation_queries']} relation queries, "
          f"stop={res['stop_reason']})")
    return 0


def cmd_mcp(args) -> int:
    """Serve the four tools over MCP stdio (blocks until the client disconnects)."""
    from oncall_brain.mcp_server import main as serve

    serve()
    return 0


def cmd_stats(args) -> int:
    st = db.stats()
    print(f"graph      : {db.GRAPH_NAME} @ {db.HOST}:{db.PORT}")
    print(f"nodes      : {st['nodes']}")
    print(f"assertions : {st['edges']}")
    print(f"evidence   : {st['evidence']}")
    print("by predicate:")
    for p, c in sorted(st["per_predicate"].items()):
        print(f"  {p:<18} {c}")
    print("by source:")
    for s, c in sorted(st["per_source"].items()):
        print(f"  {s:<50} {c}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="OnCall Brain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="rebuild the graph from corpus/")
    p.add_argument("--no-llm", action="store_true",
                   help="skip LLM extraction of free-text sources")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("approve", help="approve all candidate assertions")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("ask", help="ask a question, answered from the graph")
    p.add_argument("question")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("mcp", help="serve the tools as an MCP server (stdio)")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("stats", help="graph size and source distribution")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
