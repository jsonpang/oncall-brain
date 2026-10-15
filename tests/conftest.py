"""Shared fixtures for the OnCall Brain test suite.

The tests run against a scratch graph (`oncall_brain_test`) built from the
deterministic extractors only -- no LLM is called anywhere in the suite, and
`aether_spike` is never touched. Set ONCALL_TEST_GRAPH to override.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The default suite is offline. Destructive integration fixtures require an
# explicit scratch-graph opt-in; never erase a graph selected by the app .env.

from oncall_brain import db, schema as S  # noqa: E402
from oncall_brain.ingest import deterministic  # noqa: E402


@pytest.fixture(scope="session")
def graph():
    """A freshly built deterministic graph, all assertions approved."""
    if os.environ.get("ONCALL_ALLOW_TEST_GRAPH") != "1" or db.GRAPH_NAME != "oncall_brain_test":
        pytest.skip("offline run: destructive scratch-graph fixture is disabled")
    db.wipe()
    db.ensure_indexes()
    payload, rejects = deterministic.extract()
    db.batch_write(payload)
    approved = db.approve_all()
    assert approved > 0, "no assertions approved -- ingest wrote nothing"
    assert not rejects, f"deterministic ingest rejected {rejects[:2]}"
    yield db.g()
    db.wipe()


@pytest.fixture(scope="session")
def now() -> str:
    return "2026-10-15"


def rows(g, cypher: str, **params):
    return g.query(cypher, params).result_set


def ids(g, cypher: str, **params) -> set[str]:
    return {r[0] for r in rows(g, cypher, **params)}
