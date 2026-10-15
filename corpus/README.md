# AetherPulse Operations Knowledge Corpus (Fictionalized Enterprise Dataset)

## 1. Overview
This corpus represents the complete internal operations documentation, organizational structure, ticket lifecycle records, and daily technical collaboration logs for **AetherPulse Cloud Platform**, the next-generation DevOps, cloud engineering, and AI-assisted development platform developed for **OmniGlobal Group** (a large enterprise group).

The dataset captures the operational dynamics of a large enterprise IT ecosystem where frontline support, escalation engineering, core platform developers, field operations, and client regional subsidiaries interact daily.

---

## 2. Enterprise Architecture & Organizational Ecosystem

### 2.1 The Core Organizations
- **OmniGlobal Group (Client Enterprise)**: Enterprise client operating across autonomous regional subsidiaries. Submits operational inquiries, pipeline requirements, production issues, and compliance audits.
- **AetherPulse Cloud Platform**: Unified enterprise DevOps suite integrating CI/CD pipelines, container runtime environments, artifact storage, zero-trust security SDP, AI coding copilots, and cloud developer workstations.
- **Three Primary On-Site Service Vendors**:
  - **Synthetix Solutions (SYN)**: Primary technical delivery partner providing L1 support specialists, L2 infrastructure engineers, and dedicated pipeline escalation engineers.
  - **Strata Technologies (STR)**: Managed services partner providing frontline customer support agents, quality supervision auditors, and secondary tier operations personnel.
  - **Northwind Field Services (NFS)**: On-site field support partner providing mobile field specialists, platform enablement, and regional customer support personnel.

### 2.2 Operational Tiers & Groups
The support organization is structured into four primary operational groups, collaborating with external platform engineering:
1. **Operations Governance & QA Group (Governance & QA)**: Responsible for overall SLA governance, weekly performance reviews, quality inspection of tickets, and service-level deductions. Oversees Synthetix, Strata, and Northwind delivery quality.
2. **Frontline Support Team (L1 Frontline Support)**: Handles incoming inquiries via real-time collaboration channels, portal tickets, and regional customer issues. Performs initial intake, triage, standard script resolution, and formal escalation.
3. **Operations Engineering Team (L2 Specialized Operations)**: Deep technical specialists assigned across specific functional modules (e.g., Pipeline Engine, Container Platform, Artifact Repository). Investigates complex configuration issues and verifies release deployments. (Tier 3 Core Platform Engineering operates as an external development team for codebase defect fixes and architectural enhancements).
4. **Field & Enablement Team (Field & Enablement)**: Mobile specialist corps providing dedicated on-site enablement, deep platform adoption, regional customer hand-holding across the 12 regional operating territories, and cross-tier technical training.

### 2.3 Structural Nuances & Multi-Role Affiliations
- **Dual Affiliation**: Key senior engineers hold primary engineering positions within L2 functional modules while concurrently serving in the Field & Enablement Team as Training Directors or Field Leads.
- **Multi-Vendor Coexistence**: All three vendors (Synthetix, Strata, Northwind) maintain designated Project Managers (PMs) and operational leads. Team members from all vendors work side-by-side within shared escalation channels.
- **Regional Operating Territories**: 12 autonomous regional subsidiaries (e.g., Lakeside Delta, Northland Metro, Sunward Metro, Golden Coast, Northern Plains, Silver Peak, Emerald Coast, Frost River, Riverland Basin, Pine Ridge, Iron Bay, Amber Valley) each maintain designated regional interface contacts and project owners.

---

## 3. Real-World Operational Tensions & Characteristics
To serve as a high-fidelity evaluation benchmark for AI agents and Graph RAG architectures, this corpus explicitly preserves real-world organizational messiness:
- **Formal Procedure vs. Informal Practice**: The formal governance manual mandates that all escalations flow strictly through the Sentinel Ticket Portal (`Sentinel Desk`). However, frontline L1 engineers frequently bypass portal tickets to directly message known L2 engineers via direct chat to achieve faster resolution for high-touch users, despite risking reprimands in weekly review meetings.
- **Supervision & Escalation Dilemma**: Escalating tickets to L2 increases L1 ticket turnaround metrics but subjects L1 team leads to scrutiny during weekly governance reviews if escalation ratios exceed target thresholds. Conversely, unassigned private messages place no formal SLA response obligation on L2 engineers.
- **Lagging Registers & Transition Ambiguities**: Team rosters, documentation maintainers, and access approval lists exhibit realistic temporal lag. In some documents, departed personnel are still referenced in historical footnotes while new assignees handle live field requests.
- **Typo-style Abbreviations & Colloquialisms**: Team discussions contain informal shorthands (`KB`, `PR`, `SOP`, `UAT`, `SDP`, `PaaS`, `SYN`, `STR`, `NFS`, `L1/L2`), occasional mistyped flags, and fast-paced operational banter.
- **Duplicate Names & Disambiguation**: Includes different individuals sharing similar or identical names across distinct functional domains, requiring graph agents to disambiguate context via organizational edges and territorial scope.

---

## 4. Directory Structure
```
corpus/
├── README.md               # System setting, organizational ontology, and design notes
├── scan.py                 # Automated privacy, de-identification & compliance scanner
├── docs/                   # 10 formal operational guidelines, SOPs, and technical runbooks
│   ├── 01-service-closed-loop-mechanism-v3.1.md
│   ├── 02-ops-workflow-training-guide.md
│   ├── 03-standard-faq-response-scripts.md
│   ├── 04-l2-weekly-report-and-kb-standards.md
│   ├── 05-l2-release-acceptance-sop.md
│   ├── 06-platform-troubleshooting-faq.md
│   ├── 07-new-hire-onboarding-packet.md
│   ├── 08-module-knowledge-base-registry.md
│   ├── 09-incident-escalation-and-lifecycle-policy.md
│   └── 10-issue-taxonomy-and-routing-standards.md
├── org/                    # Comprehensive organizational context, rosters, and relations
│   ├── teams_and_responsibilities.md
│   ├── routing_and_points_of_contact.md
│   ├── organizational_hierarchy.md
│   └── contacts_roster.json
├── tickets/                # Production tickets & incident records (JSONL and CSV)
│   ├── tickets.jsonl
│   └── tickets.csv
└── chats/                  # 18 daily operational channel chat logs ([HH:MM] Name: text)
    ├── chat_2026-08-07.md
    ├── chat_2026-08-10.md
    ├── chat_2026-08-18.md
    ├── chat_2026-08-20.md
    ├── chat_2026-08-21.md
    ├── chat_2026-08-26.md
    ├── chat_2026-09-01.md
    ├── chat_2026-09-03.md
    ├── chat_2026-09-07.md
    ├── chat_2026-09-08.md
    ├── chat_2026-09-09.md
    ├── chat_2026-09-10.md
    ├── chat_2026-09-11.md
    ├── chat_2026-09-14.md
    ├── chat_2026-09-15.md
    ├── chat_2026-09-24.md
    ├── chat_2026-10-10.md
    └── chat_2026-10-12.md
```
