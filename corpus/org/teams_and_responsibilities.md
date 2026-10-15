# Teams and Responsibilities Specification (Field Support & Operations)

> **Governance Notice**: This operational context document is maintained manually by operational leads. Official directory extracts do not record field team sub-assignments, informal mentoring links, or immediate onboarding changes. Each team lead maintains their respective division; updates must be refreshed with every personnel adjustment.

---

## 1. Primary Operational Units

| Team | Team Lead | Operational Scope | Mission Statement | Effective Date | Governance Status |
|---|---|---|---|---|---|
| Field & Enablement Team | Arthur Vance | 12 Regional Operating Territories (Platform Adoption & Deep Technical Enablement) | Overcome regional onboarding friction, accelerate platform adoption across regional subsidiaries, conduct pre-visit readiness reviews, on-site live coaching, and post-visit support retrospectives. | 2026-07-01 | Confirmed |
| Operations Governance & QA Group | Gregory Stone | Entire Support Organization & Multi-Vendor Operations | Establish SLA standards, inspect ticket compliance, audit escalation validity, manage vendor evaluation scorecards, and enforce daily check-in discipline. | 2026-05-01 | Confirmed |
| Frontline Support Team (L1) | Julian Thorne | Sentinel Desk & Frontline Channels | First-line intake, triage, standard script resolution, and formal escalation across all 12 regional subsidiaries. | 2026-06-15 | Confirmed |
| Operations Engineering Team (L2) | Marcus Sterling | Core Platform Modules (L2 Specialized Operations) | Deep module engineering, root-cause troubleshooting, release acceptance, and defect lifecycle escalation to external L3 platform developers. | 2026-05-01 | Confirmed |

---

## 2. Field & Enablement Team - Regional Assignments

> **Source**: Field Engineering Roster updated following the departure of Tristan Hayes on 2026-09-25. Regional assignments reflect operational dashboard workload balancing. Names formatted as `Primary (Assisting)` denote primary accountability with secondary backup. Sean Porter took over Golden Coast and Sunward Metro following Walter Kane's departure on 2026-09-03.

| Member | Primary Assigned Territories | Assisting Territories | Operational Notes | Status |
|---|---|---|---|---|
| Ethan Drake [formerly Derek Ward] | Lakeside Delta, Silver Peak | None | Transitioned to assume primary lead for Silver Peak on 2026-09-25 taking over from Tristan Hayes. | Confirmed |
| Hannah Reed | Northland Metro, Amber Valley | None | High-density metropolitan and strategic enterprise accounts. | Confirmed |
| Owen Wright | Pine Ridge, Iron Bay | None | Joined on 2026-07-16 to backfill capacity. | Confirmed |
| Zachary Price | Frost River, Riverland Basin | None | Transitioned to assume primary lead for Frost River and Riverland Basin on 2026-09-25 taking over from Tristan Hayes. | Confirmed |
| Frank Stewart | Northern Plains, Emerald Coast | None | Primary regional coverage for industrial accounts. | Confirmed |
| Sean Porter | Golden Coast, Sunward Metro | None | Replaced Walter Kane on 2026-09-03 (including Sector E co-management); currently in shadowing period; comprehensive module qualification scheduled for mid-October. | Confirmed |

**Current Active Headcount**: 6 field engineers covering the 12 regional operating territories.  
*Data Discrepancy Note*: Core progress dashboards and operational canvas monitors still list Walter Kane for Golden Coast due to upstream database sync delay.

---

## 3. Personnel Departures & Historical Transitions

| Personnel | Onboarding Date | Departure Date | Former Assigned Territories | Successor | Verification Source |
|---|---|---|---|---|---|
| Tristan Hayes | 2026-08-18 | 2026-09-25 | Frost River, Riverland Basin, Silver Peak | Zachary Price (Frost River, Riverland Basin); Ethan Drake (Silver Peak) | Arthur Vance verbal confirmation and operational handover memo (2026-09-25). |
| Walter Kane | 2026-04-01 | 2026-09-03 | Golden Coast, Sunward Metro | Sean Porter | Arthur Vance field restructuring record D1 (2026-09-03). |
| Peter Walsh | 2026-02-14 | 2026-07-16 | Pine Ridge, Iron Bay | Owen Wright | Arthur Vance field restructuring record D2 (2026-07-16). |

---

## 4. Formal Operational Rulings

> Formal rulings apply strictly within their defined administrative boundaries. They do not automatically modify enterprise-wide HR records but govern daily routing and triage.

| Ruling ID | Ruling Category | Subject | Determination Content | Scope of Application | Authorized Lead | Effective Date | Review Date |
|---|---|---|---|---|---|---|---|
| D1 | Departure Confirmation | Walter Kane | Formally departed; Sean Porter assumes all operational and regional responsibilities. | Field & Enablement Team | Arthur Vance | 2026-09-03 | Indefinite |
| D2 | Departure Confirmation | Peter Walsh | Formally departed; Owen Wright assumes operational duties. | Field & Enablement Team | Arthur Vance | 2026-07-16 | Indefinite |
| D3 | Roster Exemption | Field Group Members | Group assignments defined by this document take precedence over central directory synchronizations. | Field & Enablement Team | Arthur Vance | 2026-09-28 | 2026-11-03 |
| D4 | Group Affiliation | Hannah Reed | Officially assigned to Field & Enablement Team. | Field & Enablement Team | Arthur Vance | 2026-09-28 | Indefinite |
| D5 | Directory Waiver | Transitional / Part-time Roles | Directory entries for transitional assignments are taken as-is; unverified flags handled by respective operational leads. | Escalation Review Queue | Arthur Vance | 2026-09-28 | 2026-11-03 |
| D6 | Departure Confirmation | Tristan Hayes | Formally departed; Frost River and Riverland Basin transferred to Zachary Price; Silver Peak transferred to Ethan Drake. | Field & Enablement Team | Arthur Vance | 2026-09-25 | Indefinite |

---

## 5. Granular Functional Responsibilities (Sub-Scope Rulings)

> Responsibility is partitioned at the sub-feature level: accountability for a sub-scope does NOT equate to owning an entire platform module.

| Engineer | Functional Role | Scope Definition | Current Status | Basis | Sign-Off Parties |
|---|---|---|---|---|---|
| Arthur Vance | Field & Enablement Team Lead | Overall leadership of field operations across the 12 regional territories. | Active | Operational charter | Self |
| Arthur Vance | Knowledge Hub - Tier 2 | Transitional owner for upcoming Knowledge Hub release. | Transitional | Operational charter | Self |
| Arthur Vance | Continuous Testing Agent - Tier 2 | Responsible strictly for the Agent execution component of Continuous Testing; does NOT handle overall test management. | Active | Operational charter | Self |
| Arthur Vance | Mobile Build Runner - Pre-requisite Agent Deployment - Tier 2 | Before utilizing Mobile Build Service, users must deploy a dedicated agent runner. Pre-requisite deployment issues route to him; core Mobile Build engine issues route elsewhere. | In Handover (to Wyatt Lawson) | Operational handover | Arthur Vance, Wyatt Lawson |
| Wyatt Lawson | Mobile Build Runner - Pre-requisite Agent Deployment - Tier 2 | Incoming assignee taking over from Arthur Vance. Until full handover sign-off, dual escalation to Arthur Vance remains valid. | In Handover (Receiver) | Operational handover | Arthur Vance, Wyatt Lawson |
| Colin Marsh | Pipeline Engine - Tier 2 | Full operational ownership of Pipeline orchestration, runner triggers, and DAG syntax parsing. | Active | Ruling by Arthur Vance | Colin Marsh |
| Lucas Vance | Continuous Deployment (CD) - Tier 2 | Container deployment, cluster synchronization, and release strategies. | Active | Ruling by Arthur Vance | Lucas Vance |
| Wyatt Lawson | Continuous Integration (CI) - Tier 2 | Build steps, code compilation, and dependency caching. | Active | Ruling by Arthur Vance | Wyatt Lawson |

*Directory Discrepancy Note*: Central directory extracts still reflect Arthur Vance and Wyatt Lawson under legacy combined titles `Tier 2 / CI, CD, Pipeline`. Daily operational routing follows this document.
