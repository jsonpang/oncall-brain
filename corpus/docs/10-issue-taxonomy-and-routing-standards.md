# AetherPulse Issue Taxonomy and Routing Standards

## 1. Primary Classification Taxonomy
Every operational ticket, inquiry, and defect logged within AetherPulse must be classified into one of the following 6 standardized categories:

1. **Technical Consultation (CONSULT)**: How-to questions regarding platform usage, configuration parameters, or documentation clarity.
2. **Environment & Configuration (CONFIG)**: Resource quota increases, network policy openings, permission grant requests, and tenant bindings.
3. **Feature Enhancement Request (REQ_FEAT)**: User proposals for new features, UI enhancements, or capability extensions.
4. **Data Extraction & Reporting (REQ_DATA)**: Requests for historical execution logs, performance statistics, or custom SQL data reports.
5. **Production Software Defect (DEFECT)**: Software errors, unhandled 500 exceptions, broken pipeline stages, or UI rendering bugs.
6. **Platform System Outage (OUTAGE)**: Severe infrastructure failures, widespread runner disconnections, or platform database failures.

---

## 2. Decision Tree for Disambiguation
- *Does the behavior deviate from the official platform documentation?*
  - **YES**: Classify as `DEFECT`.
  - **NO**:
    - *Is the user asking how to perform an action?* -> Classify as `CONSULT`.
    - *Is the user requesting access rights or firewall port access?* -> Classify as `CONFIG`.
    - *Is the user proposing an unreleased capability?* -> Classify as `REQ_FEAT`.
    - *Is the user requesting a custom export of database metrics?* -> Classify as `REQ_DATA`.

---

## 3. Unresolved Routing Policy
When an issue spans multiple modules or involves cross-vendor dependencies:
1. The receiving specialist must NOT reject or close the ticket.
2. Post ticket reference in `Channel-General-OpsL1L2` and tag the Duty Lead for joint triage.
3. Transfer ticket to the module that represents the earliest failure point in the execution chain.
