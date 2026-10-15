# Operational Points of Contact & Escalation Routing (Who-to-Find Guide)

> **Purpose**: Serves as the primary routing guide for support agents, frontline triage personnel, and AI assistants. When encountering operational questions, consult this registry first to minimize disruption to senior operational managers (e.g., Marcus Sterling, Sophia Evans).
> **Status Conventions**: Items marked `[Channel Answer, Pending Formal Confirmation]` represent verified operational practices gathered from internal engineering channels that have not yet been formalized into corporate SOPs. Once formally verified, status shifts to `[Confirmed]`.

---

## 1. Core Module Escalation Rulings (Overrides Directory Lag)

When directory extracts show outdated module owners, routing follows these binding operational determinations:

| Functional Module | Tier 2 Escalation Lead | Authorizing Lead | Effective Date | Status |
|---|---|---|---|---|
| Pipeline Engine | Colin Marsh | Arthur Vance | 2026-10-04 | Confirmed |
| Continuous Deployment (CD) | Lucas Vance | Arthur Vance | 2026-10-04 | Confirmed |
| Continuous Integration (CI) | Wyatt Lawson | Arthur Vance | 2026-10-04 | Confirmed |
| Dev Workspace / Cloud Desktops | Seth Miller [formerly Philip Dean] | Eleanor Hughes | 2026-09-15 | Confirmed |
| Artifact Repository & Registries | Wayne Douglas | Marcus Sterling | 2026-09-01 | Confirmed |

---

## 2. Permissions, Access & Administrative Requests

| Operational Request / Problem | Escalation Contact | Operational Guidance & Nuance | Source Context | Status |
|---|---|---|---|---|
| Tier 2 engineer lacks upload permissions for Knowledge Hub | Wesley Tate | Knowledge Hub curation tasks are scheduled under his supervision; reach out to grant upload privilege. | Ops Engineering Channel | Channel Answer, Pending Formal Confirmation |
| Requesting Platform Administrator role allocation | Dominic Shaw | Administrator roles are heavily restricted. Triage must clarify business justification and tenant scope before escalating. | Ops Engineering Channel | Confirmed (Verified by Arthur Vance) |
| Incorrect permission menu permissions | Luke Fischer (Unified Gateway & Portal) | Route requests for menu permission adjustments to the Unified Gateway engineering queue. | Ops Supervision Channel | Channel Answer, Pending Formal Confirmation |
| Cannot delete files uploaded by others in shared channel drive | Wayne Douglas; or use workaround: create new subfolder, move files into subfolder, and delete the entire subfolder | Second method is a known system workaround for platform file locking bugs. | CICD L1-L2 Collaboration Channel | Channel Answer, Pending Formal Confirmation |
| Accessing Unified Development Studio | Colin Marsh | Access requires explicit project onboarding whitelisting prior to web URL accessibility. | Ops Supervision Channel | Channel Answer, Pending Formal Confirmation |
| PulseVPN access token expired | Unified extension granted until 2026-09-30; no automated expiration alert; users must track personal expiration date | Master extension list published in announcements. If expired after September 30, must submit renewal request. | Ops Engineering Channel | Expired, Pending Schedule Update |

---

## 3. External Systems & Cross-Team Integrations

| Operational Scenario | Routing / Action | Clarification & Context | Source Context | Status |
|---|---|---|---|---|
| Submitting defect ticket affecting CloudInfra Subsidiary | No personal contact needed: submit via standard AetherPulse single defect template | CloudInfra operates as a designated module within AetherPulse; module engineers triage directly. | CICD L1-L2 Collaboration Channel | Confirmed |
| Feature requests for Management Domain | Post inquiry in General Ops L1-L2 Channel or submit formal Sentinel ticket | Do not message individual engineers directly for untriaged cross-domain feature requests. | Ops Engineering Channel | Confirmed |
| Tenant onboarding to AegisPaaS missing OpenAPI endpoints | Marcus Sterling coordinates joint channel with AegisPaaS engineering | No single permanent contact assigned; requires management mediation. | CICD L1-L2 Collaboration Channel | Channel Answer, Pending Formal Confirmation |
| Determining whether tenant repository resides in TitanRegistry vs AegisPaaS | Advise tenant developer to obtain cluster node info and base image path from their own platform admin | Support team does not host user private cluster topologies. | Daily Operations Briefing | Confirmed |
| Opening network firewall policies for Continuous Deployment | Contact CD Lead (Lucas Vance); for artifact replication policies, consult Artifact Management team | Artifact team provides standard network port declaration form. | Daily Operations Briefing | Confirmed |
| End-user requesting Cloud Dev Desktop allocation | Direct user to check internal corporate approval email; if missing, consult user's internal project director | Tenant internal administrative process outside platform support scope. | Daily Operations Briefing | Confirmed |

---

## 4. Shift Operations, Quality Governance & Channels

| Governance Activity / Inquiry | Escalation Lead | Standard Operating Procedure | Source Context | Status |
|---|---|---|---|---|
| Unsure who is on active duty for current shift | Inquire in general channel for the designated Duty Officer | Daily Duty Roster published on internal operations board. | Ops Engineering Channel | Confirmed |
| Project acceptance checklist | Engineering infrastructure projects: Hazel Woods; Production live-net projects: Renee Yates | External tenant project leads should consult their management prior to submitting acceptance sign-offs. | Ops Engineering Channel | Confirmed |
| Daily morning report of unverified / absent support check-ins | Seraphina Lin | Frontline specialists handling Sentinel tickets and live chat queues must complete daily check-in; channel-only specialists are exempt. | Ops Engineering Channel | Confirmed |
| Operational portal service degradation / outages | Standard daily issues: Strata Partner Ops Channel; Critical P1 outages: Top Escalation Channel | The legacy portal feedback channel was deprecated due to lack of dedicated engineering triage. | Daily Operations Briefing | Confirmed |
| Platform Certification & Assessment Lead | Internal Training & Enablement Group | Training team organizes testing sessions; proctoring supported by vendor team leads. | Ops Supervision Channel | Confirmed |
| Manuals for Account & Workspace Management | Seth Miller [formerly Philip Dean] | Assigned to Dev Workspace & Design Management module. | General Ops L1-L2 Channel | Confirmed |

---

## 5. Unresolved Channel Inquiries (Real-World Ambiguities)

The following operational questions were logged in live channels but lacked conclusive answers at time of documentation capture:
1. *"Which engineer owns this sub-module?"* (Channel log: 2026-08-03): Responded with *"unknown"* because the module name was missing from the query and unlisted in central directory.
2. *"Who handles Northland Metro regional module issues?"* (Channel log: 2026-07-13): No definitive answer provided in chat; logged as routing gap.
3. *"The Sentinel browser plugin ticket submission category hierarchy is incorrect, who adjusts this?"* (Channel log: 2026-08-06): Discussion yielded no formal owner; ticket remained untriaged.
4. *"This issue belongs outside our support group module, where should we redirect?"* (Channel log: 2026-08-10): Message scrolled past by high-priority check-in announcements without resolution.
