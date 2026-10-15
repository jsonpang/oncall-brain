"""Offline regression tests: no graph connection and no provider calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

os.environ["GRAPH_NAME"] = "oncall_brain"
from eval import run_gold as gold
from oncall_brain import agent, db, tools
from oncall_brain.ingest import llm, run, claims


class AgentStateTests(unittest.TestCase):
    def ask_with(self, question, answer, result):
        call = NS(id="t1", function=NS(name="search_knowledge", arguments='{"query":"Orchid"}'))
        messages = [NS(tool_calls=[call], content=None), NS(tool_calls=[], content=answer)]
        client = NS(chat=NS(completions=NS(create=lambda **kw: NS(choices=[NS(message=messages.pop(0))]))))
        with patch.object(agent, "_openai_client", return_value=client), patch.object(tools, "call_tool", return_value=result):
            return agent.ask(question, max_calls=2)

    def evidence(self):
        return {"answer_status": "applicable", "assertions": [{"aid": "a1", "evidence": ["e1"]}],
                "evidence": [{"id": "e1", "source": "corpus/docs/synthetic.md", "locator": "7",
                              "excerpt": "Orchid owner is Cedar."}]}

    def test_uncited_fact_is_replaced_with_search_and_gap_only(self):
        result = self.ask_with("Who owns Orchid?", "Status: applicable\nContact an invented owner.", self.evidence())
        self.assertEqual(result["answer_status"], "not_found")
        self.assertNotIn("invented owner", result["answer"])
        self.assertIn("Orchid", result["answer"])

    def test_missing_specific_identifier_cannot_borrow_related_owner(self):
        result = self.ask_with("Who owns Orchid-Edge-Sync?",
            "Status: applicable\nCedar owns it [a1 -> corpus/docs/synthetic.md:7]", self.evidence())
        self.assertEqual(result["answer_status"], "not_found")
        self.assertNotIn("Cedar", result["answer"])

    def test_supported_fact_uses_one_canonical_locator(self):
        result = self.ask_with("Who owns Orchid?",
            "Status: applicable\nCedar [a1 -> corpus/docs/synthetic.md:7 -> corpus/docs/synthetic.md:7]", self.evidence())
        self.assertEqual(result["answer_status"], "applicable")
        self.assertIn("[a1 \u2192 corpus/docs/synthetic.md:7]", result["answer"])
        self.assertEqual(result["answer"].count("corpus/docs/synthetic.md:7"), 1)

    def test_not_found_discards_even_cited_fallback_advice(self):
        result = self.ask_with("Who owns Orchid?",
            "Status: not_found\nAsk Cedar [a1 -> corpus/docs/synthetic.md:7]", self.evidence())
        self.assertEqual(result["answer_status"], "not_found")
        self.assertNotIn("Cedar", result["answer"])

    def test_status_comes_from_tools(self):
        self.assertEqual(agent.answer_status([]), "insufficient_context")
        for state in ("applicable", "conflict", "superseded", "insufficient_context", "not_found"):
            self.assertEqual(agent.answer_status([{"answer_status": state}]), state)
        self.assertEqual(agent.answer_status([{"status": "error"}]), "insufficient_context")

    def test_agent_overrides_guessed_label_from_real_tool_state(self):
        call = NS(id="t1", function=NS(name="search_knowledge", arguments='{"query":"Orchid"}'))
        messages = [NS(tool_calls=[call], content=None),
                    NS(tool_calls=[], content="Status: applicable\nNo owner established.")]
        client = NS(chat=NS(completions=NS(create=lambda **kw: NS(choices=[NS(message=messages.pop(0))]))))
        with patch.object(agent, "_openai_client", return_value=client), \
             patch.object(tools, "call_tool", return_value={"status": "not_found", "answer_status": "not_found"}):
            result = agent.ask("Who owns Orchid?", max_calls=2)
        self.assertEqual(result["answer"].splitlines()[0], "Status: not_found")
        self.assertEqual(len(result["tool_results"]), 1)

    def test_main_state_must_have_tool_support(self):
        data = [{"answer_status": "conflict", "assertions": [{"aid": "a1"}],
                 "conflicts": [{"subject": "Module:Other", "values": [{"aid": "b1"}, {"aid": "b2"}]}]}]
        self.assertEqual(agent.answer_status(data, proposed="applicable"), "applicable")
        self.assertEqual(agent.answer_status(data, proposed="superseded"), "conflict")


class SourceRetrievalTests(unittest.TestCase):
    def test_source_context_survives_entity_extraction_miss(self):
        ev = {"id": "ev:corpus/docs/synthetic.md:7", "source": "corpus/docs/synthetic.md",
              "locator": "7", "excerpt": "Use an empathetic tone.", "authority": "formal_rule"}
        def query(q, params):
            if q == tools.TEMPLATES.get("source_search"):
                return NS(result_set=[[ev[k] for k in ("id", "source", "locator", "excerpt", "authority")]])
            if q == tools.TEMPLATES["evidence_by_id"]:
                return NS(result_set=[[ev[k] for k in ("id", "source", "locator", "excerpt", "authority")]])
            return NS(result_set=[])
        with patch.object(tools, "g", return_value=NS(query=query)):
            result = tools.search_knowledge("tone")
        self.assertTrue(result["source_evidence"])
        self.assertEqual(result["source_evidence"][0]["id"], ev["id"])


class ConflictTests(unittest.TestCase):
    def claim(self, **changes):
        claim = dict(subject="Module:Orchid", predicate="cache_limit", value="96 MiB",
                     condition={"environment": "stage"}, authority="formal_rule",
                     valid_from="2026-01-01", valid_to=None, aid="official", evidence=["e1"])
        claim.update(changes)
        return claim

    def test_formal_and_observation_disagreement_is_conflict(self):
        claims = [self.claim(), self.claim(value="192 MiB", aid="chat",
                                           authority="observation", evidence=["e2"])]
        conflicts = tools.compare_claims(claims)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual([v["authority"] for v in conflicts[0]["values"]],
                         ["formal_rule", "observation"])
        self.assertEqual(conflicts[0]["values"][1]["evidence"], ["e2"])

    def test_different_conditions_times_subjects_and_slots_do_not_conflict(self):
        for changes in ({"condition": {"environment": "production"}},
                        {"valid_from": "2027-01-01"}, {"subject": "Module:Azalea"},
                        {"predicate": "request_timeout"}):
            first = self.claim(valid_to="2026-12-31")
            second = self.claim(value="192 MiB", **changes)
            self.assertFalse(tools.compare_claims([first, second]))

    def test_unconditional_overlaps_and_equal_values_are_not_conflict(self):
        self.assertTrue(tools.compare_claims([
            self.claim(condition={}), self.claim(value="192 MiB")]))
        self.assertFalse(tools.compare_claims([self.claim(), self.claim(aid="duplicate")]))

    def test_multivalued_facts_and_different_granularity_are_not_conflict(self):
        self.assertFalse(tools.compare_claims([
            self.claim(predicate="reference", value="guide-a", cardinality="many"),
            self.claim(predicate="reference", value="guide-b", cardinality="many")]))
        self.assertFalse(tools.compare_claims([
            self.claim(predicate="routing", value="team-a", value_kind="team"),
            self.claim(predicate="routing", value="person-b", value_kind="person")]))

    def test_unproven_text_exclusivity_and_registered_multivalues_are_not_conflict(self):
        self.assertFalse(tools.compare_claims([
            self.claim(predicate="location", value="Resource:Portal"),
            self.claim(predicate="location", value="Resource:Portal/Module/Guide")]))
        self.assertFalse(tools.compare_claims([
            self.claim(predicate="MAINTAINED_BY", value="Person:Juniper"),
            self.claim(predicate="MAINTAINED_BY", value="Person:Cedar")]))

    def test_explicit_single_value_and_scalar_duty_are_compared(self):
        self.assertTrue(tools.compare_claims([
            self.claim(predicate="routing", value="Person:Juniper", cardinality="one"),
            self.claim(predicate="routing", value="Person:Cedar", cardinality="one")]))

    def test_curation_does_not_imply_technical_routing(self):
        self.assertFalse(tools.compare_claims([
            self.claim(predicate="MAINTAINED_BY", value="Person:Juniper"),
            self.claim(predicate="TECH_LEAD_OF", value="Person:Cedar")]))
        self.assertTrue(tools.compare_claims([
            self.claim(predicate="technical_routing", value="Person:Juniper"),
            self.claim(predicate="technical_routing", value="Person:Cedar", authority="observation")]))

    def test_context_excludes_inapplicable_claims(self):
        claims = [self.claim(), self.claim(value="192 MiB", authority="observation")]
        self.assertFalse(tools.compare_claims(claims, {"environment": "production"}))

    def test_relation_query_applies_conditions(self):
        row = [["Procedure:Orchid", "Rule:Quota"], ["Procedure", "Rule"], ["Orchid", "Quota"],
               ["GOVERNED_BY"], ["a1"], ["formal_rule"], [["e1"]], ["2026-01-01"],
               ["region=east"], [None]]
        def query(q, params):
            return NS(result_set=[row] if q.startswith("MATCH p =") else [])
        with patch.object(tools, "g", return_value=NS(query=query)), \
             patch.object(tools, "_evidence", return_value=[]):
            self.assertEqual(tools.query_relations("Procedure:Orchid", ["GOVERNED_BY"])["answer_status"],
                             "insufficient_context")

    def test_read_and_query_surface_semantic_conflicts_with_evidence(self):
        claims = [self.claim(), self.claim(value="192 MiB", aid="chat", authority="observation")]
        slot_rows = [["Rule:Orchid", "Module:Orchid", c["aid"], c["authority"],
                      c["valid_from"], c["valid_to"], json.dumps([dict(c, evidence_id="e1")]), ["e1"]]
                     for c in claims]
        def query(q, params):
            if q == tools.TEMPLATES["read_entity"]:
                rows = [[{"id": "Module:Orchid", "kind": "Module"}]]
            elif q == tools.TEMPLATES["slot_claims"]:
                rows = slot_rows
            else:
                rows = []
            return NS(result_set=rows)
        with patch.object(tools, "g", return_value=NS(query=query)), \
             patch.object(tools, "_evidence", side_effect=lambda ids: list(ids)):
            for result in (tools.read_knowledge("Module:Orchid"),
                           tools.query_relations("Module:Orchid", ["APPLIES_TO"])):
                self.assertEqual(result["answer_status"], "conflict")
                self.assertEqual(len(result["conflicts"]), 1)
                self.assertIn("e1", result["evidence"])


class IngestTests(unittest.TestCase):
    def edge(self, **changes):
        value = dict(subject="Procedure:Deploy", predicate="GOVERNED_BY",
                     object="Rule:Quota", source="synthetic.md", line=1,
                     authority="formal_rule", valid_from="2026-01-01",
                     valid_to=None, props={})
        value.update(changes)
        return value

    def build(self, edges):
        def ev(source, line, authority, text):
            return dict(id=f"{source}:{line}", source=source, locator=str(line),
                        excerpt="synthetic", authority=authority)
        with patch.object(run.deterministic, "extract", return_value=([], [])), \
             patch.object(run.EV, "build", side_effect=ev):
            return run.build_payload(edges, [])[0]

    def test_alignment_includes_known_ids_absent_from_edges(self):
        edges = [self.edge(object="Resource:Knowledge_Hub_MOD_88")]
        aligned, aliases = llm.canonicalise(edges, {"Knowledge Hub MOD-88"})
        self.assertEqual(aligned[0]["object"], "Knowledge Hub MOD-88")
        self.assertEqual(aliases["Resource:Knowledge_Hub_MOD_88"], "Knowledge Hub MOD-88")

    def test_alignment_does_not_merge_different_kinds(self):
        edges = [self.edge(subject="Rule:Same", object="Resource:Same")]
        aligned, _ = llm.canonicalise(edges, {"Rule:Same"})
        self.assertNotEqual(aligned[0]["subject"], aligned[0]["object"])

    def test_every_new_endpoint_has_search_properties(self):
        payload = self.build([self.edge(), self.edge(subject="Notice:Change",
                            predicate="SUPERSEDES")])
        nodes = {p["id"]: p for p in payload if p["op"] == "node"}
        for nid, kind in (("Procedure:Deploy", "Procedure"), ("Rule:Quota", "Rule"),
                          ("Notice:Change", "Notice")):
            self.assertEqual(nodes[nid]["props"]["kind"], kind)
            self.assertTrue(nodes[nid]["props"]["name"])

    def test_deterministic_evidence_references_are_materialised(self):
        edge = dict(op="edge", subject="MOD-88", predicate="HAS_RESOURCE", object="Resource:Orchid",
                    aid="a1", authority="formal_rule", evidence=["ev:corpus/docs/synthetic.md:7"],
                    valid_from="2026-01-01", valid_to=None, props={})
        evidence = dict(id=edge["evidence"][0], source="corpus/docs/synthetic.md", locator="7",
                        excerpt="synthetic", authority="formal_rule")
        with patch.object(run.deterministic, "extract", return_value=([edge], [])), \
             patch.object(run.EV, "build", return_value=evidence):
            payload = run.build_payload([], [])[0]
        self.assertTrue(any(p["op"] == "evidence" and p["id"] == evidence["id"] for p in payload))

    def test_distinct_provenance_conditions_and_times_survive(self):
        variants = [{}, {"source": "other.md"}, {"authority": "observation"},
                    {"props": {"condition": "region=east"}},
                    {"valid_from": "2026-06-01"}, {"valid_to": "2026-09-01"}]
        edges = [self.edge(**v) for v in variants]
        assertions = [p for p in self.build(edges) if p["op"] == "edge"]
        self.assertEqual(len(assertions), len(variants))
        self.assertEqual(len({p["aid"] for p in assertions}), len(variants))

    def test_identical_semantics_merge_evidence(self):
        edges = [self.edge(), self.edge(line=2), self.edge()]
        assertions = [p for p in self.build(edges) if p["op"] == "edge"]
        self.assertEqual(len(assertions), 1)
        self.assertEqual(len(assertions[0]["evidence"]), 2)

    def test_edge_fallback_sets_endpoint_properties(self):
        fake = NS(query=lambda query, params: captured.append((query, params)))
        captured = []
        with patch.object(db, "g", return_value=fake):
            db.edge("Rule:Quota", "APPLIES_TO", "MOD-88", aid="a", authority="formal_rule",
                    evidence_ids=["e"])
        query, params = captured[0]
        self.assertIn("a.kind", query)
        self.assertIn("b.name", query)

    def test_semantic_annotation_requires_real_catalog_and_quote(self):
        payload = [dict(op="node", id="Module:Orchid", props={"name": "Orchid"}),
                   dict(op="evidence", id="e1", source="corpus/docs/synthetic.md", locator="1",
                        excerpt="Cache limit is 96 MiB.", authority="formal_rule"),
                   dict(op="edge", aid="a1", evidence=["e1"], props={})]
        good = dict(evidence_id="e1", subject="Module:Orchid", predicate="cache_limit",
                    value="96 MiB", condition={}, quote="96 MiB")
        data = {"claims": [good, dict(good, quote="unsupported"),
                           dict(good, subject="Module:Invented")]}
        client = NS(chat=NS(completions=NS(create=lambda **kw: NS(
            choices=[NS(message=NS(content=json.dumps(data)))]))))
        with tempfile.TemporaryDirectory(dir=gold.ROOT / "eval") as tmp, \
             patch.object(llm, "CACHE_DIR", Path(tmp)), \
             patch.object(llm, "_llm", return_value=client), \
             patch.object(run.EV, "lines", return_value=["Cache limit is 96 MiB."]):
            claims.annotate(payload)
        saved = json.loads(payload[-1]["props"]["claims_json"])
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["value"], "96 MiB")


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=gold.ROOT / "eval")
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.patches = [patch("openai.OpenAI", side_effect=RuntimeError("offline")),
                        patch.object(gold, "_disk_cache", return_value=self.cache),
                        patch.dict(os.environ, LLM_MODEL="synthetic-judge",
                                   LLM_JUDGE_MODEL="synthetic-judge",
                                   LLM_BASE_URL="https://invalid.example", LLM_API_KEY="test")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        if hasattr(gold, "_forbidden_cache"):
            gold._forbidden_cache.clear()

    def response(self, value):
        client = NS(chat=NS(completions=NS(create=lambda **kw: NS(
            choices=[NS(message=NS(content=json.dumps(value)))]))))
        return patch("openai.OpenAI", return_value=client)

    def test_forbidden_cache_miss_success_and_reuse(self):
        with self.response({"violates": False}):
            self.assertIs(gold._llm_forbidden_check("p", "a", "q"), False)
        with patch("openai.OpenAI", side_effect=RuntimeError("unavailable")):
            self.assertIs(gold._llm_forbidden_check("p", "a", "q"), False)
        self.assertEqual(len(list(self.cache.glob("*.json"))), 1)

    def test_judge_failure_invalid_and_not_cached(self):
        q = dict(question="q", expected_status="applicable", points=["a"],
                 forbidden=["p"], evidence=[])
        res = {"result": {"answer": "Status: applicable\na"},
               "inspect": {"citations": ["fabricated -> corpus/docs/fake.md:7"]}}
        with patch("openai.OpenAI", side_effect=RuntimeError("unavailable")):
            result = gold.judge(q, res)
        self.assertEqual(result["detected"], "judge_error")
        self.assertFalse(result["valid"])
        self.assertFalse(result["passed"])
        self.assertFalse(list(self.cache.glob("*.json")))

    def test_strict_boolean_and_complete_indices(self):
        for data in ({"results": [{"index": 1, "covered": "false"}]},
                     {"results": []},
                     {"results": [{"index": 1, "covered": True}] * 2},
                     {"results": [{"index": True, "covered": True}]}):
            with self.subTest(data=data), self.response(data):
                with self.assertRaises(Exception):
                    gold._llm_point_hits("q", "a", ["p"])
        for field, fn in (("violates", lambda: gold._llm_forbidden_check("p", "a", "q")),
                          ("carries", lambda: gold._llm_carries("conflict", "q", "a"))):
            with self.subTest(field=field), self.response({field: "false"}):
                with self.assertRaises(Exception):
                    fn()
        self.assertFalse(list(self.cache.glob("*.json")))

    def test_fenced_json_preserves_strict_types(self):
        response = NS(choices=[NS(message=NS(content='```json\n{"violates": "false"}\n```'))])
        client = NS(chat=NS(completions=NS(create=lambda **kw: response)))
        with patch("openai.OpenAI", return_value=client):
            data = gold._judge_json("synthetic")
        self.assertEqual(data["violates"], "false")
        with self.assertRaises(gold.JudgeError):
            gold._boolean(data, "violates")

    def test_cache_bound_to_model_prompt_and_schema(self):
        first = gold._cache_path("points", "payload")
        with patch.dict(os.environ, LLM_JUDGE_MODEL="another-model"):
            self.assertNotEqual(first, gold._cache_path("points", "payload"))
        with patch.object(gold, "_POINT_JUDGE", gold._POINT_JUDGE + " changed"):
            self.assertNotEqual(first, gold._cache_path("points", "payload"))
        with patch.object(gold, "JUDGE_SCHEMA_VERSION", "next", create=True):
            self.assertNotEqual(first, gold._cache_path("points", "payload"))

    def test_status_is_exact_first_line(self):
        for state in ("applicable", "insufficient_context", "conflict", "superseded", "not_found"):
            self.assertEqual(gold._llm_detect_status("q", f"Status: {state}\nconflict superseded"), state)
        with self.assertRaises(Exception):
            gold._llm_detect_status("q", "## conflict\nNo explicit status")

    def test_fabricated_citation_not_accepted(self):
        q = dict(question="q", expected_status="applicable", points=["a"],
                 forbidden=[], evidence=["corpus/docs/a.md:7"])
        res = {"result": {"answer": "Status: applicable\na [fake -> corpus/docs/a.md:7]"},
               "inspect": {"citations": ["fake -> corpus/docs/a.md:7"]}}
        with self.response({"results": [{"index": 1, "covered": True}]}):
            result = gold.judge(q, res)
        self.assertFalse(result["passed"])
        self.assertFalse(result["citations"])

    def test_exact_tool_evidence_citation_is_accepted(self):
        q = dict(question="q", expected_status="applicable", points=["a"], forbidden=[],
                 evidence=["corpus/docs/synthetic.md:7"])
        res = {"result": {"answer": "Status: applicable\na [a1 -> corpus/docs/synthetic.md:7]",
                          "tool_results": [{"assertions": [{"aid": "a1", "evidence": ["e1"]}],
                                            "evidence": [{"id": "e1", "source": "corpus/docs/synthetic.md", "locator": "7"}]}]},
               "inspect": {"citations": []}}
        with self.response({"results": [{"index": 1, "covered": True}]}):
            result = gold.judge(q, res)
        self.assertTrue(result["passed"])
        self.assertEqual(result["evidence_hit"], q["evidence"])
        self.assertFalse(gold._evidence_cited("corpus/docs/other.md:7", q["evidence"]))

    def test_direct_source_citation_must_match_retrieved_evidence(self):
        ev = dict(id="ev:corpus/docs/synthetic.md:7", source="corpus/docs/synthetic.md", locator="7")
        res = {"answer": "Status: applicable\n[ev:corpus/docs/synthetic.md:7 -> corpus/docs/synthetic.md:7]",
               "tool_results": [{"source_evidence": [ev]}]}
        valid, invalid = gold._verified_citations(res)
        self.assertEqual(len(valid), 1)
        self.assertFalse(invalid)
        res["answer"] = res["answer"].replace(" -> corpus/docs/synthetic.md:7", " -> corpus/docs/synthetic.md:8")
        self.assertTrue(gold._verified_citations(res)[1])

    def test_unicode_citation_and_embedded_aid_arrows_are_exact(self):
        aid = "det:OWNER:Person:Cedar->Module:Orchid:corpus/docs/synthetic.md:7"
        res = {"answer": f"Status: applicable\nCedar [{aid} \u2192 corpus/docs/synthetic.md:7]",
               "tool_results": [{"assertions": [{"aid": aid, "evidence": ["e1"]}],
                                 "evidence": [{"id": "e1", "source": "corpus/docs/synthetic.md", "locator": "7"}]}]}
        self.assertEqual(len(gold._verified_citations(res)[0]), 1)
        res["answer"] = "Status: applicable\nCedar [a-made-up \u2192 corpus/docs/synthetic.md:7]"
        self.assertTrue(gold._verified_citations(res)[1])

    def test_duplicate_locator_is_rejected_by_judge(self):
        res = {"answer": "Status: applicable\nCedar [a1 -> corpus/docs/synthetic.md:7 -> corpus/docs/synthetic.md:7]",
               "tool_results": [{"assertions": [{"aid": "a1", "evidence": ["e1"]}],
                                 "evidence": [{"id": "e1", "source": "corpus/docs/synthetic.md", "locator": "7"}]}]}
        self.assertTrue(gold._verified_citations(res)[1])

    def test_late_judge_failure_does_not_cache_partial_question(self):
        q = dict(question="q", expected_status="applicable", points=["a"], forbidden=["p"], evidence=[])
        with patch.object(gold, "_judge_json", side_effect=[
                {"results": [{"index": 1, "covered": True}]}, gold.JudgeError("offline")]):
            result = gold.judge(q, {"result": {"answer": "Status: applicable\na"},
                                    "inspect": {"citations": []}})
        self.assertFalse(result["valid"])
        self.assertFalse(list(self.cache.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
