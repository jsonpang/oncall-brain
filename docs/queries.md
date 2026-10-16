# Cypher query inventory

All queries are server-owned. The model chooses arguments, predicates, direction,
and hop count. Only registered predicates are accepted. Names in backticks below
are literal `tools.TEMPLATES` keys; other names describe inline queries rather
than additional registry entries. Locations are relative to the repository root.

| Name | Purpose | File:function |
|---|---|---|
| `fulltext_search` / `fulltext_search_kind` | Indexed entity search, optionally by kind | `oncall_brain/tools.py:search_knowledge`; also `oncall_brain/fast.py:_candidates` |
| `id_prefix` / `id_prefix_kind` | Case-insensitive id substring fallback | `oncall_brain/tools.py:search_knowledge`; also `oncall_brain/fast.py:_candidates` |
| `read_entity` | Entity properties | `oncall_brain/tools.py:read_knowledge` |
| `read_neighbors` | Visible incident assertions in both directions | `oncall_brain/tools.py:read_knowledge` |
| `superseded_targets` | Replacement ids, assertion ids, effective dates | `oncall_brain/tools.py:_superseded` |
| `traverse` | Up to three hops with per-relationship visibility | `oncall_brain/tools.py:query_relations` |
| `evidence_by_id` | Resolve evidence and neighbouring thread lines | `oncall_brain/tools.py:_evidence`, `_with_context`, `_nearby_sources` |
| `source_search` | Verbatim evidence substring search | `oncall_brain/tools.py:_source_rows` |
| `slot_claims` | Retrieve structured claims for relevant subjects/objects | `oncall_brain/tools.py:_slot_conflicts` |
| `claim_value_kinds` | Resolve value entity kinds before comparison | `oncall_brain/tools.py:_slot_conflicts` |
| Source-line catalogue | Read Evidence once for IDF-weighted retrieval | `oncall_brain/tools.py:related_source_lines` |
| Assertion evidence lookup | Resolve evidence on a validated relation pattern by aid | `oncall_brain/tools.py:_rel_evidence` |
| Expertise ticket-handler join | Join ABOUT and HANDLED_BY with visibility on both | `oncall_brain/tools.py:module_expertise` |
| Expertise owner neighbours | TECH_LEAD_OF / MAINTAINED_BY alongside history | `oncall_brain/tools.py:module_expertise` |
| Health evidence locations | Evidence ids to source:line | `oncall_brain/health.py:_sources` |
| Health entity total | Count Entity nodes | `oncall_brain/health.py:_totals` |
| Health assertion total | Count stored non-EVIDENCE relationships | `oncall_brain/health.py:_totals` |
| Health ticket total | Count Ticket nodes | `oncall_brain/health.py:_totals` |
| Health lead coverage | Visible TECH_LEAD_OF, grouped in Python | `oncall_brain/health.py:_single_points` |
| Health module owners | Visible TECH_LEAD_OF per module | `oncall_brain/health.py:_owner_vs_handlers` |
| Health module handlers | ABOUT / HANDLED_BY join across modules | `oncall_brain/health.py:_owner_vs_handlers` |
| Health chat-only rules | Rule/Notice incident evidence, then source lookup | `oncall_brain/health.py:_chat_only_rules` |
| Health conflict subjects | All Entity ids for shared slot-conflict checks | `oncall_brain/health.py:_conflicts` |
| Health single-valued slots | Visible objects per registered single-valued predicate | `oncall_brain/health.py:_conflicts` |
| Health replacements (reuse) | Shared superseded_targets query | `oncall_brain/health.py:_superseded_list`, `kb_health` |
| Impact module list | Module ids/names ordered by id | `oncall_brain/impact.py:get_all_modules` |
| Impact module lookup | Exact id, then case-insensitive id fallback | `oncall_brain/impact.py:module_impact` |
| Impact owners | Reverse TECH_LEAD_OF | `oncall_brain/impact.py:module_impact` |
| Impact teams | Owner's MEMBER_OF edges | `oncall_brain/impact.py:module_impact` |
| Impact resources | Module HAS_RESOURCE edges | `oncall_brain/impact.py:module_impact` |
| Impact rules | Reverse APPLIES_TO | `oncall_brain/impact.py:module_impact` |
| Impact tickets | Reverse ABOUT, plus shared expertise/evidence queries | `oncall_brain/impact.py:module_impact` |
| Impact territories | Territory catalogue to reconcile ticket-text names | `oncall_brain/impact.py:module_impact` |
| Impact territory contacts | Reverse COVERS_TERRITORY | `oncall_brain/impact.py:module_impact` |
| Procedure members | HAS_STEP, optionally filtered by procedure id | `oncall_brain/sequence.py:_members` |
| Procedure ordering | PRECEDES / PARALLEL_WITH between member ids | `oncall_brain/sequence.py:_ordering` |
| Procedure root | Validate procedure id/kind and read name | `oncall_brain/sequence.py:procedure_steps` |
| Procedure metadata | GOVERNED_BY / USES_RESOURCE / PERFORMED_BY / REQUIRES_FIELD | `oncall_brain/sequence.py:procedure_steps` |
| Demo target validation | Existing Rule/Notice kind | `oncall_brain/freshness.py:add_notice` |
| Demo duplicate ids | Notice/evidence id collision check | `oncall_brain/freshness.py:add_notice` |
| Demo authority | Authority of an existing approved superseding notice | `oncall_brain/freshness.py:add_notice` |
| Demo inherited context | Visible neighbours along SUPERSEDES chains | `oncall_brain/freshness.py:add_notice` |
| Demo atomic insertion | MATCH old/context; CREATE Notice, Evidence, SUPERSEDES and scope edges | `oncall_brain/freshness.py:add_notice` |
| Demo copy / reset (graph commands) | GRAPH.COPY / GRAPH.DELETE, not Cypher | `oncall_brain/freshness.py:clone_demo_graph`, `reset_demo` |
| Ingest indexes | Fulltext indexes on Entity name/id | `oncall_brain/db.py:ensure_indexes` |
| Ingest entity / evidence upsert | MERGE nodes and set properties | `oncall_brain/db.py:node`, `evidence_node` |
| Ingest assertion upsert | MERGE endpoints and relation by aid; set assertion properties | `oncall_brain/db.py:edge` |
| Candidate approval | SET candidate state to approved, all or per predicate | `oncall_brain/db.py:approve_all`, `approve_predicate` |
| Graph statistics | Entity/evidence/assertion counts and predicate/source distribution | `oncall_brain/db.py:stats` |
| Evaluation graph fingerprint | Ordered relationship/property read for provenance hash | `eval/run_gold.py:main` |
