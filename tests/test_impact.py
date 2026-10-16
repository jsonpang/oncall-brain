"""Offline tests for module impact analysis (oncall_brain/impact.py).

Follows the pattern of tests/test_health.py: `tools.g` is replaced with a fake graph
whose query() dispatches based on the Cypher query structure.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from oncall_brain import impact, tools
from web.app import app

NOW = "2026-10-16"


class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class FakeGraph:
    """Answers queries issued by impact.py and tools.module_expertise."""

    def __init__(self, *, modules=(), tech_leads=(), teams=(), resources=(), rules=(),
                 about_tickets=(), handled_by=(), territories=(), coverage=(), evidence=()):
        self.modules = list(modules)            # (id, name, kind)
        self.tech_leads = list(tech_leads)      # (person_id, person_name, person_kind, mod_id, aid, [ev], auth)
        self.teams = list(teams)                # (person_id, team_id, team_name, team_kind, aid, [ev], auth)
        self.resources = list(resources)        # (mod_id, res_id, res_name, res_kind, aid, [ev], auth)
        self.rules = list(rules)                # (rule_id, rule_name, rule_kind, mod_id, aid, [ev], auth)
        self.about_tickets = list(about_tickets)  # (ticket_id, mod_id, aid, [ev], auth)
        self.handled_by = list(handled_by)      # (ticket_id, person_id, person_name, aid, [ev], auth)
        self.territories = list(territories)    # (territory_id, territory_name)
        self.coverage = list(coverage)          # (person_id, person_name, person_kind, terr_id, aid, [ev], auth)
        self.evidence = dict(evidence)          # id -> (source, locator, excerpt, authority)

    def query(self, cypher: str, params: dict | None = None) -> FakeResult:
        params = params or {}

        # 1. get_all_modules
        if "MATCH (m:Entity) WHERE m.kind = 'Module' RETURN m.id, m.name" in cypher:
            return FakeResult([[m[0], m[1]] for m in sorted(self.modules, key=lambda x: x[0])])

        # 2. Target module lookup
        if "MATCH (m:Entity {id: $id}) RETURN m.id, m.name, m.kind" in cypher or \
           "toUpper(m.id) = toUpper($id)" in cypher:
            target_id = params.get("id")
            for m in self.modules:
                if m[0] == target_id or m[0].upper() == str(target_id).upper():
                    return FakeResult([[m[0], m[1], m[2]]])
            return FakeResult([])

        # 3. module_expertise queries
        if "(k:Entity)-[a:ABOUT]->(m:Entity {id: $id}), (k)-[h:HANDLED_BY]->(p:Entity)" in cypher:
            target_id = params.get("id")
            rows = []
            for tid, mid, aaid, aev, aauth in self.about_tickets:
                if mid != target_id:
                    continue
                # Find module name
                m_name = next((m[1] for m in self.modules if m[0] == mid), mid)
                for h_tid, pid, pname, haid, hev, hauth in self.handled_by:
                    if h_tid == tid:
                        rows.append([tid, "Ticket", tid, "Module", m_name, pid, "Person", pname,
                                     aaid, aauth, aev, haid, hauth, hev])
            return FakeResult(rows)

        if "TECH_LEAD_OF|MAINTAINED_BY" in cypher:
            target_id = params.get("id")
            rows = []
            for pid, pname, pkind, mid, aid, ev, auth in self.tech_leads:
                if mid == target_id:
                    rows.append(["TECH_LEAD_OF", pid, pname, aid])
            return FakeResult(rows)

        # 4. Owners TECH_LEAD_OF query in impact.py
        if "MATCH (p:Entity)-[r:TECH_LEAD_OF]->(m:Entity {id: $id})" in cypher:
            target_id = params.get("id")
            rows = []
            for pid, pname, pkind, mid, aid, ev, auth in self.tech_leads:
                if mid == target_id:
                    rows.append([pid, pname, pkind, aid, ev, auth])
            return FakeResult(rows)

        # 5. Member of teams
        if "MATCH (p:Entity {id: $pid})-[r:MEMBER_OF]->(tm:Entity)" in cypher:
            target_pid = params.get("pid")
            rows = []
            for pid, tmid, tmname, tmkind, aid, ev, auth in self.teams:
                if pid == target_pid:
                    rows.append([tmid, tmname, tmkind, aid, ev, auth])
            return FakeResult(rows)

        # 6. Resources HAS_RESOURCE
        if "MATCH (m:Entity {id: $id})-[r:HAS_RESOURCE]->(res:Entity)" in cypher:
            target_id = params.get("id")
            rows = []
            for mid, resid, resname, reskind, aid, ev, auth in self.resources:
                if mid == target_id:
                    rows.append([resid, resname, reskind, aid, ev, auth])
            return FakeResult(rows)

        # 7. Rules APPLIES_TO
        if "MATCH (rule:Entity)-[r:APPLIES_TO]->(m:Entity {id: $id})" in cypher:
            target_id = params.get("id")
            rows = []
            for rid, rname, rkind, mid, aid, ev, auth in self.rules:
                if mid == target_id:
                    rows.append([rid, rname, rkind, aid, ev, auth])
            return FakeResult(rows)

        # 8. ABOUT edges query
        if "MATCH (k:Entity)-[r:ABOUT]->(m:Entity {id: $id})" in cypher:
            target_id = params.get("id")
            rows = []
            for tid, mid, aid, ev, auth in self.about_tickets:
                if mid == target_id:
                    rows.append([tid, aid, ev, auth])
            return FakeResult(rows)

        # 9. Evidence query
        if "MATCH (e:Evidence) WHERE e.id IN $ids" in cypher:
            ids = params.get("ids", [])
            rows = []
            for i in ids:
                if i in self.evidence:
                    src, loc, exc, auth = self.evidence[i]
                    rows.append([i, src, loc, exc, auth])
            return FakeResult(rows)

        # 10. Graph territories query
        if "MATCH (t:Entity) WHERE t.kind = 'Territory' RETURN t.id, t.name" in cypher:
            return FakeResult([[t[0], t[1]] for t in self.territories])

        # 11. COVERS_TERRITORY query
        if "MATCH (p:Entity)-[r:COVERS_TERRITORY]->(t:Entity {id: $tid})" in cypher:
            target_tid = params.get("tid")
            rows = []
            for pid, pname, pkind, tid, aid, ev, auth in self.coverage:
                if tid == target_tid:
                    rows.append([pid, pname, pkind, aid, ev, auth])
            return FakeResult(rows)

        raise AssertionError(f"unexpected cypher query: {cypher}")


def sample_graph():
    return FakeGraph(
        modules=[
            ("MOD-01", "Pipeline Orchestration Engine", "Module"),
            ("MOD-02", "Continuous Integration", "Module"),
        ],
        tech_leads=[
            ("Colin Marsh", "Colin Marsh", "Person", "MOD-01", "det:TECH_LEAD_OF:Colin Marsh->MOD-01",
             ["ev:routing:14"], "formal_rule")
        ],
        teams=[
            ("Colin Marsh", "Ops-L2", "Operations Engineering Team (L2)", "Team",
             "det:MEMBER_OF:Colin Marsh->Ops-L2", ["ev:roster:65"], "formal_rule")
        ],
        resources=[
            ("MOD-01", "Resource:MOD-01-KB", "Pipeline SOP", "Resource",
             "det:HAS_RESOURCE:MOD-01->Resource:MOD-01-KB", ["ev:kb:17"], "formal_rule")
        ],
        rules=[
            ("Rule:StepTimeout", "Step Timeout Default", "Rule", "MOD-01",
             "llm:rule-step-timeout", ["ev:chat:26"], "observation")
        ],
        about_tickets=[
            ("TCK-1001", "MOD-01", "det:ABOUT:TCK-1001->MOD-01", ["ev:tck:1"], "observation"),
            ("TCK-1002", "MOD-01", "det:ABOUT:TCK-1002->MOD-01", ["ev:tck:2"], "observation")
        ],
        handled_by=[
            ("TCK-1001", "Frank Stewart", "Frank Stewart", "det:HANDLED_BY:TCK-1001", ["ev:tck:1"], "observation"),
            ("TCK-1002", "Frank Stewart", "Frank Stewart", "det:HANDLED_BY:TCK-1002", ["ev:tck:2"], "observation")
        ],
        territories=[
            ("Sunward Metro", "Sunward Metro"),
            ("Emerald Coast", "Emerald Coast")
        ],
        coverage=[
            ("Dominic Shaw", "Dominic Shaw", "Person", "Sunward Metro",
             "llm:coverage:Dominic->Sunward", ["ev:chat:8"], "observation"),
            ("Frank Stewart", "Frank Stewart", "Person", "Emerald Coast",
             "det:coverage:Frank->Emerald", ["ev:teams:28"], "formal_rule")
        ],
        evidence={
            "ev:routing:14": ("corpus/org/routing.md", "14", "Routing doc line 14", "formal_rule"),
            "ev:roster:65": ("corpus/org/roster.json", "65", '{"name": "Colin Marsh"}', "formal_rule"),
            "ev:kb:17": ("corpus/docs/kb.md", "17", "KB SOP line 17", "formal_rule"),
            "ev:chat:26": ("corpus/chats/chat1.md", "26", "Step timeout discussion", "observation"),
            "ev:chat:8": ("corpus/chats/chat2.md", "8", "Dominic on Sunward duty", "observation"),
            "ev:teams:28": ("corpus/org/teams.md", "28", "Frank covers Emerald Coast", "formal_rule"),
            "ev:tck:1": (
                "corpus/tickets/tickets.jsonl",
                "1",
                json.dumps({
                    "ticket_id": "TCK-1001",
                    "regional_territory": "Sunward Metro Territory",
                    "user_tenant": "Sunward Metro Research Institute",
                    "module_name": "Pipeline Orchestration Engine"
                }),
                "observation"
            ),
            "ev:tck:2": (
                "corpus/tickets/tickets.jsonl",
                "2",
                json.dumps({
                    "ticket_id": "TCK-1002",
                    "regional_territory": "Sunward Metro Territory",
                    "user_tenant": "Sunward Metro Research Institute",
                    "module_name": "Pipeline Orchestration Engine"
                }),
                "observation"
            )
        }
    )


class ImpactUnitTests(unittest.TestCase):
    """Unit tests for impact analysis logic."""

    def test_module_impact_full_aggregation(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            res = impact.module_impact("MOD-01")

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["module"]["id"], "MOD-01")
        self.assertEqual(res["module"]["name"], "Pipeline Orchestration Engine")

        # 1. Owners and Teams
        self.assertEqual(len(res["owners"]), 1)
        owner = res["owners"][0]
        self.assertEqual(owner["id"], "Colin Marsh")
        self.assertIn("det:TECH_LEAD_OF:Colin Marsh->MOD-01", owner["aids"])
        self.assertEqual(len(owner["teams"]), 1)
        self.assertEqual(owner["teams"][0]["id"], "Ops-L2")

        # 2. Resources
        self.assertEqual(len(res["resources"]), 1)
        self.assertEqual(res["resources"][0]["id"], "Resource:MOD-01-KB")

        # 3. Rules
        self.assertEqual(len(res["rules"]), 1)
        self.assertEqual(res["rules"][0]["id"], "Rule:StepTimeout")

        # 4. Tickets & Handlers
        self.assertEqual(res["tickets_count"], 2)
        self.assertEqual(len(res["handlers"]), 1)
        self.assertEqual(res["handlers"][0]["id"], "Frank Stewart")
        self.assertEqual(res["handlers"][0]["count"], 2)

        # 5. Affected Territories & Tenants (parsed from ticket excerpts)
        self.assertEqual(len(res["affected_territories"]), 1)
        terr = res["affected_territories"][0]
        self.assertEqual(terr["territory"], "Sunward Metro Territory")
        self.assertEqual(terr["canonical_id"], "Sunward Metro")
        self.assertEqual(terr["count"], 2)
        self.assertEqual(terr["source_note"], "来自工单原文，不是图关系")
        self.assertEqual(terr["tickets"], ["TCK-1001", "TCK-1002"])

        self.assertEqual(len(res["affected_tenants"]), 1)
        tenant = res["affected_tenants"][0]
        self.assertEqual(tenant["tenant"], "Sunward Metro Research Institute")
        self.assertEqual(tenant["count"], 2)
        self.assertEqual(tenant["source_note"], "来自工单原文，不是图关系")

        # 6. Notify leads (COVERS_TERRITORY reverse lookup)
        self.assertEqual(len(res["notify_leads"]), 1)
        lead = res["notify_leads"][0]
        self.assertEqual(lead["id"], "Dominic Shaw")
        self.assertIn("Sunward Metro Territory", lead["territories"])

        # 7. Evidence
        ev_ids = {e["id"] for e in res["evidence"]}
        self.assertIn("ev:routing:14", ev_ids)
        self.assertIn("ev:tck:1", ev_ids)

    def test_module_without_tickets(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            res = impact.module_impact("MOD-02")

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["module"]["id"], "MOD-02")
        self.assertEqual(res["tickets_count"], 0)
        self.assertEqual(res["handlers"], [])
        self.assertEqual(res["affected_territories"], [])
        self.assertEqual(res["affected_tenants"], [])
        self.assertEqual(res["notify_leads"], [])

    def test_module_not_found(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            res = impact.module_impact("MOD-999")

        self.assertEqual(res["status"], "not_found")
        self.assertEqual(res["tickets_count"], 0)

    def test_get_all_modules(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            modules = impact.get_all_modules()

        self.assertEqual(len(modules), 2)
        self.assertEqual(modules[0]["id"], "MOD-01")
        self.assertEqual(modules[1]["id"], "MOD-02")


class ImpactWebAPITests(unittest.TestCase):
    """Test Flask endpoints for impact analysis."""

    def setUp(self):
        self.client = app.test_client()

    def test_api_modules_endpoint(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            resp = self.client.get("/api/modules")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["id"], "MOD-01")

    def test_api_impact_endpoint_found(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            resp = self.client.get("/api/impact/MOD-01")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["module"]["id"], "MOD-01")
            self.assertIn("affected_territories", data)
            self.assertIn("notify_leads", data)

    def test_api_impact_endpoint_not_found(self):
        fake = sample_graph()
        with patch.object(tools, "g", return_value=fake), \
             patch.object(impact.tools, "g", return_value=fake):
            resp = self.client.get("/api/impact/MOD-NONEXISTENT")
            self.assertEqual(resp.status_code, 404)
            data = resp.get_json()
            self.assertEqual(data["status"], "not_found")

    def test_health_page_contains_impact_section(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("sec-impact", html)
        self.assertIn("impact-mod-select", html)
        self.assertIn("content-impact", html)
