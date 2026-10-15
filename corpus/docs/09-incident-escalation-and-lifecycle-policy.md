# AetherPulse Incident Escalation & Lifecycle Policy

## 1. Incident Severity Definitions & Timelines
- **P1 (Critical)**: Core service down, widespread runner outage, or critical data corruption. Initial response: <= 5 minutes. Escalation to L2/L3: immediate. Resolution target: <= 2 hours.
- **P2 (Major)**: Significant feature degradation affecting a full regional subsidiary. Initial response: <= 15 minutes. Escalation to L2: <= 30 minutes. Resolution target: <= 4 hours.
- **P3 (Moderate)**: Localized issue with available workaround. Initial response: <= 30 minutes. Resolution target: <= 24 hours.
- **P4 (Minor)**: Cosmetic issue, general question, or documentation error. Initial response: <= 1 hour. Resolution target: <= 3 business days.

---

## 2. Six-Stage Incident Lifecycle
1. **Intake & Triage**: Frontline specialist acknowledges report, validates user identity, records regional territory, and assigns initial severity.
2. **Diagnostic Evaluation**: Frontline checks Knowledge Hub runbooks. If solvable with standard fix, resolve immediately.
3. **Escalation**: If unresolved within SLA threshold, ticket is formally transferred to designated Tier 2 module lead via Sentinel Portal.
4. **Engineering Remediation**: Tier 2 investigates; if codebase defect is found, hand off to Tier 3 platform development with reproduction test case.
5. **Verification & Delivery**: Tier 2 tests patch in staging; validates with user in production.
6. **Post-Incident Review (PIR) & KB Authoring**: Tier 2 authors retrospective analysis for P1/P2 incidents and submits new runbook to Knowledge Hub within 5 business days.
