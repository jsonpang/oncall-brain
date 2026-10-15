"""Expose the same four read-only tools over MCP (stdio transport).

Run with `python -m oncall_brain.mcp_server`. Tool behaviour lives in tools.py,
so the MCP surface, the CLI and the agent all answer from one code path.
Uses the MCP SDK 2.x high-level MCPServer interface.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp.server import MCPServer  # noqa: E402

from . import tools  # noqa: E402

server = MCPServer("oncall-brain",
                   instructions="AetherPulse operations knowledge graph. "
                                "Call describe_ontology first, then search_knowledge "
                                "to resolve ids, then read_knowledge / query_relations.")


@server.tool()
def describe_ontology() -> dict:
    """Entity kinds, registered predicates, authority levels, behaviour rules."""
    return tools.describe_ontology()


@server.tool()
def search_knowledge(query: str, kind: str | None = None, limit: int = 12) -> dict:
    """Find entity candidates (id/kind/name) by name."""
    return tools.search_knowledge(query, kind, limit)


@server.tool()
def read_knowledge(id: str, context: dict | None = None) -> dict:
    """Read one entity: properties plus all currently visible assertions with evidence."""
    return tools.read_knowledge(id, context)


@server.tool()
def query_relations(start_id: str, predicates: list[str], direction: str = "out",
                    max_hops: int = 1, context: dict | None = None,
                    limit: int = 50) -> dict:
    """Traverse 1-3 hops along registered predicates; every hop is visibility-filtered."""
    return tools.query_relations(start_id, predicates, direction, max_hops,
                                 context, limit)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
