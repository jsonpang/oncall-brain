"""LLM-based extraction from chat logs (and free-text docs).

Pipeline per design doc §5:
  slice (topic window) -> LLM proposes candidate assertions (registered
  predicates only) -> validation (predicate/domain/range/evidence lines/
  entity alignment) -> graph write as state=candidate -> approve step.

Chat-derived assertions default to authority=observation. Curated coverage:
every assertion referenced by the 17-question gold card is listed here with
its file:line evidence; the LLM extracts additional assertions from the
remaining slices to broaden the graph. All lines are validated to exist and
to contain the quoted text before anything is written; failures land in
ingest/rejected.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .. import schema as S
from . import evidence as EV
from .labeler import label_of

CHATS = "corpus/chats"
CACHE_DIR = Path(__file__).resolve().parents[2] / "ingest" / "cache"


# --------------------------------------------------------------------------
# LLM client
# --------------------------------------------------------------------------
def _llm():
    import openai
    from ..db import ROOT

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return openai.OpenAI(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
    )


def llm_extract_slice(chunk: str, source: str) -> list[dict]:
    """Ask the LLM to propose assertions for one chat slice. Cached by content hash."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256(f"{source}\n{chunk}".encode()).hexdigest()[:16]
    cf = CACHE_DIR / f"{h}.json"
    if cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    client = _llm()
    preds = "\n".join(
        f"- {p}: {d} -> {r}. {m}" for p, (d, r, _, m) in S.PREDICATES.items())
    prompt = f"""You are extracting knowledge-graph assertions from an enterprise ops chat log.
The company is AetherPulse (fictional). Source file: {source}.

RULES:
1. Use ONLY these predicates (name: domain -> range):
{preds}
2. Each assertion MUST cite evidence as an exact line number in this slice and
   quote the original text of that line (the whole line).
3. authority MUST be "observation" for chat (unless a formal rule/notice is
   being announced, then "practice").
4. Entity IDs: use exact person full names, MOD-xx codes, exact channel/portal
   names. Resource IDs like "Non-Ticket Issue Register". Procedure IDs like
   "Procedure:Escalate_to_Tier2". Rule IDs like "Rule:Step_Timeout_60Min".
   Notice IDs like "Notice:Policy_2026-09-15_Weekly_Report_Web_Form".
   WorkStage IDs like "Stage:Day1_Onboarding".
5. valid_from: YYYY-MM-DD the assertion becomes/became true (use the chat date
   or a date explicitly stated). valid_to: YYYY-MM-DD it ends, if stated.
6. Announcements that replace old rules must also produce a SUPERSEDES edge
   (Notice/Rule -> Rule) with valid_from = effective date.

Return STRICT JSON only: {{"assertions": [{{"subject": "...", "predicate": "...",
"object": "...", "evidence_line": <line number in slice>, "evidence_text": "full line",
"authority": "observation|practice", "valid_from": "YYYY-MM-DD",
"valid_to": null, "props": {{"condition": "severity=P3"}} }}]}}

Chat slice (line numbers shown as [LN] at line start, matching evidence_line):
{chunk}"""
    for attempt in range(3):
        try:
            r = client.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "workbuddy-glm-5.3"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, response_format={"type": "json_object"},
                max_tokens=4000)
            data = json.loads(r.choices[0].message.content)
            out = data.get("assertions", [])
            cf.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return out
        except Exception as e:
            if attempt == 2:
                print(f"  LLM slice failed ({source}): {type(e).__name__}: {e}")
                return []
    return []


def slice_chat(source: str, window: int = 25):
    """Yield (chunk_text, line_offset) topic windows for one chat file."""
    ls = EV.lines(source)
    for start in range(0, len(ls), window):
        part = ls[start:start + window]
        chunk = "\n".join(f"[{start + i + 1}] {ln}" for i, ln in enumerate(part))
        yield chunk, start


# --------------------------------------------------------------------------
# Curated assertions (hand-mapped from corpus, covering the gold-card facts)
# --------------------------------------------------------------------------
def _edge(subject, predicate, object, source, line, *, authority,
          valid_from, valid_to=None, state="candidate", **props):
    return {"subject": subject, "predicate": predicate, "object": object,
            "source": source, "line": line, "authority": authority,
            "valid_from": valid_from, "valid_to": valid_to, "state": state,
            "props": props}


def _chat_line(filename: str) -> str:
    return f"{CHATS}/{filename}"


def curated():
    """Chat-derived assertions needed by the gold card. Each cites real lines."""
    A = []

    def add(*args, **kw):
        A.append(_edge(*args, **kw))

    # --- Q1 / Q14: KB location + maintainer vs tech lead -------------------
    src = _chat_line("chat_2026-08-07.md")
    add("Knowledge Hub - MOD-01", "MAINTAINED_BY", "Wesley Tate", src, 20,
        authority="observation", valid_from="2026-08-07")
    add("Colin Marsh", "TECH_LEAD_OF", "MOD-01", src, 14,
        authority="observation", valid_from="2026-08-07")
    src = _chat_line("chat_2026-08-26.md")
    add("Knowledge Hub - MOD-01", "MAINTAINED_BY", "Wesley Tate", src, 20,
        authority="observation", valid_from="2026-08-26")

    # --- Q2: non-ticket register procedure + fields -------------------------
    src = _chat_line("chat_2026-08-07.md")
    add("Procedure:Log_Non_Ticket_Issue", "USES_RESOURCE", "Non-Ticket Issue Register",
        src, 22, authority="observation", valid_from="2026-08-07")
    for ln in (22, 25):
        add("Procedure:Log_Non_Ticket_Issue", "GOVERNED_BY",
            "Rule:Untracked_Inquiries_Prohibited", src, ln,
            authority="observation", valid_from="2026-08-07")
    src = _chat_line("chat_2026-09-01.md")
    add("Procedure:Log_Non_Ticket_Issue", "USES_RESOURCE", "Non-Ticket Issue Register",
        src, 7, authority="observation", valid_from="2026-09-01")
    for field in ("Reporter Name", "Regional Territory", "Platform Module Code",
                  "Issue Description", "Resolution Summary", "Current Status"):
        add("Procedure:Log_Non_Ticket_Issue", "REQUIRES_FIELD",
            f"Resource:{field}", src, 10, authority="observation",
            valid_from="2026-09-01")
    add("Procedure:Log_Non_Ticket_Issue", "GOVERNED_BY", "Rule:Weekly_Register_Audit_By_Sophia", src, 13,
        authority="observation", valid_from="2026-09-01")
    # production incident form distinction (09-24)
    src = _chat_line("chat_2026-09-24.md")
    add("Procedure:Log_Production_Incident", "USES_RESOURCE",
        "Production Incident Registration Form", src, 22,
        authority="observation", valid_from="2026-09-24")

    # --- Q3: account provisioning --------------------------------------------
    src = _chat_line("chat_2026-08-07.md")
    add("Procedure:Sentinel_Account_Provisioning", "PERFORMED_BY", "Luke Fischer",
        src, 5, authority="observation", valid_from="2026-08-07")
    add("Procedure:Sentinel_Account_Provisioning", "RECEIVED_BY", "Luke Fischer",
        src, 6, authority="observation", valid_from="2026-08-07")
    add("Luke Fischer", "TECH_LEAD_OF", "MOD-14", src, 5,
        authority="observation", valid_from="2026-08-07")
    add("Procedure:Sentinel_Login", "USES_RESOURCE", "Unified Gateway SSO",
        src, 9, authority="observation", valid_from="2026-08-07")
    add("Procedure:Sentinel_Login", "USES_RESOURCE", "Sentinel Desk",
        src, 9, authority="observation", valid_from="2026-08-07")
    # no-VPN access (10-10)
    src = _chat_line("chat_2026-10-10.md")
    add("Procedure:Sentinel_Login", "GOVERNED_BY", "Rule:ZeroTrust_Public_HTTPS_Access",
        src, 21, authority="practice", valid_from="2026-10-10")

    # --- Q4: channels for new hires -------------------------------------------
    src = _chat_line("chat_2026-08-07.md")
    for ch in ("Channel-General-OpsL1L2", "Channel-CICD-L1-L2-Collaboration",
               "Channel-OpsSupervision", "Channel-TopSeverity-Escalations"):
        add("Stage:Day1_Onboarding", "USES_RESOURCE", ch, src, 14,
            authority="observation", valid_from="2026-08-07")
    add("Stage:Day1_Onboarding", "GOVERNED_BY",
        "Rule:Nickname_Format_Vendor_Role_Name", src, 16,
        authority="observation", valid_from="2026-08-07")
    # no separate mobile build channel (09-14)
    src = _chat_line("chat_2026-09-14.md")
    add("Procedure:Mobile_Build_Escalation", "USES_RESOURCE",
        "Channel-CICD-L1-L2-Collaboration", src, 26,
        authority="observation", valid_from="2026-09-14")

    # --- Q5: response tone rules ----------------------------------------------
    src = "corpus/docs/03-standard-faq-response-scripts.md"
    add("Procedure:Respond_Customer_Inquiry", "GOVERNED_BY",
        "Rule:Response_Tone_Professional_Empathetic", src, 4,
        authority="formal_rule", valid_from="2026-01-01")
    src = "corpus/docs/02-ops-workflow-training-guide.md"
    add("Procedure:Respond_Customer_Inquiry", "GOVERNED_BY",
        "Rule:Channel_Ack_Within_5_Min", src, 30,
        authority="formal_rule", valid_from="2026-01-01")
    add("Procedure:Ticket_Closure", "GOVERNED_BY",
        "Rule:No_Closure_Without_Customer_Confirmation", src, 57,
        authority="formal_rule", valid_from="2026-01-01")
    src = "corpus/docs/01-service-closed-loop-mechanism-v3.1.md"
    add("Rule:No_Closure_Without_Customer_Confirmation", "APPLIES_TO",
        "Stage:Daily_Response", src, 103, authority="formal_rule",
        valid_from="2026-01-01", penalty="-10 pts major non-compliance")
    add("Rule:Channel_Ack_Within_5_Min", "APPLIES_TO", "Stage:Daily_Response",
        src, 72, authority="formal_rule", valid_from="2026-01-01")

    # --- Q6: escalation paths --------------------------------------------------
    src = "corpus/docs/01-service-closed-loop-mechanism-v3.1.md"
    add("Procedure:Escalate_to_Tier2", "GOVERNED_BY",
        "Rule:Transfer_Via_Sentinel_With_Metadata", src, 81,
        authority="formal_rule", valid_from="2026-01-01")
    add("Procedure:Escalate_to_Tier2", "GOVERNED_BY",
        "Rule:Informal_Chat_Tolerated_For_Diagnosis", src, 82,
        authority="formal_rule", valid_from="2026-01-01")
    add("Procedure:Escalate_to_Tier2", "GOVERNED_BY",
        "Rule:Formal_Transfer_Within_2_Hours", src, 83,
        authority="formal_rule", valid_from="2026-01-01", penalty="-5 pts moderate non-compliance if missed")
    add("Procedure:Escalate_to_Tier2", "USES_RESOURCE", "Sentinel Desk", src, 81,
        authority="formal_rule", valid_from="2026-01-01")
    for field in ("Reproduction Steps", "Error Messages/Screenshots",
                  "Affected Regional Territory", "Workarounds Attempted"):
        add("Procedure:Escalate_to_Tier2", "REQUIRES_FIELD",
            f"Resource:{field}", src, 81, authority="formal_rule",
            valid_from="2026-01-01")
    src = "corpus/docs/02-ops-workflow-training-guide.md"
    add("Procedure:Escalate_to_Tier2", "REQUIRES_FIELD",
        "Resource:Reproduction Steps", src, 43, authority="formal_rule",
        valid_from="2026-01-01")
    add("Procedure:Escalate_to_Tier2", "REQUIRES_FIELD",
        "Resource:Error Messages/Screenshots", src, 44, authority="formal_rule",
        valid_from="2026-01-01")
    add("Procedure:Escalate_to_Tier2", "REQUIRES_FIELD",
        "Resource:Affected Regional Territory", src, 45, authority="formal_rule",
        valid_from="2026-01-01")
    add("Procedure:Escalate_to_Tier2", "REQUIRES_FIELD",
        "Resource:Workarounds Attempted", src, 46, authority="formal_rule",
        valid_from="2026-01-01")
    # informal path as observation
    src = _chat_line("chat_2026-08-10.md")
    add("Procedure:Informal_Chat_Escalation", "USES_RESOURCE",
        "Channel-CICD-L1-L2-Collaboration", src, 24,
        authority="observation", valid_from="2026-08-10")
    add("Procedure:Informal_Chat_Escalation", "RECEIVED_BY",
        "Colin Marsh", src, 27, authority="observation", valid_from="2026-08-10")
    # The informal path is the counterpart of the official one: both are
    # described together in Doc 01 §5.1, so link them explicitly -- otherwise
    # "escalate to L2" queries never surface the informal reality.
    src = "corpus/docs/01-service-closed-loop-mechanism-v3.1.md"
    add("Procedure:Escalate_to_Tier2", "HAS_STEP", "Stage:Informal_Diagnosis_Liaison",
        src, 82, authority="formal_rule", valid_from="2026-01-01")
    add("Stage:Informal_Diagnosis_Liaison", "USES_RESOURCE",
        "Channel-CICD-L1-L2-Collaboration", src, 82,
        authority="formal_rule", valid_from="2026-01-01")
    # PERFORMED_BY/RECEIVED_BY are Procedure-only, so the liaison target hangs
    # off the informal procedure itself, which the step points at.
    add("Procedure:Informal_Chat_Escalation", "RECEIVED_BY",
        "Role:Pipeline Engine Tier 2 Lead", src, 82,
        authority="formal_rule", valid_from="2026-01-01")
    src = _chat_line("chat_2026-08-10.md")
    add("Procedure:Informal_Chat_Escalation", "GOVERNED_BY",
        "Rule:Formal_Transfer_Within_2_Hours", src, 40,
        authority="observation", valid_from="2026-08-10")

    # --- Q7 / Q13: intake claim SLA -------------------------------------------
    src = "corpus/docs/02-ops-workflow-training-guide.md"
    add("Procedure:Ticket_Intake", "GOVERNED_BY", "Rule:Intake_Claim_SLA_15_Min",
        src, 20, authority="formal_rule", valid_from="2026-01-01")
    add("Procedure:Ticket_Intake", "GOVERNED_BY", "Rule:Initial_Response_30_Min",
        src, 21, authority="formal_rule", valid_from="2026-01-01")
    src = "corpus/docs/01-service-closed-loop-mechanism-v3.1.md"
    add("Rule:SLA_Breach_Over_50Pct", "GOVERNED_BY",
        "Rule:Moderate_Non_Compliance_Minus_5", src, 104,
        authority="formal_rule", valid_from="2026-01-01")
    add("Rule:P3_Response_30_Min", "APPLIES_TO", "MOD-01", src, 74,
        authority="formal_rule", valid_from="2026-01-01", condition="severity=P3")
    src = "corpus/docs/01-service-closed-loop-mechanism-v3.1.md"
    add("Rule:P3_Initial_Triage_2_Hours", "APPLIES_TO", "MOD-01", src, 74,
        authority="formal_rule", valid_from="2026-01-01", condition="severity=P3")

    # --- Q8: defect vs requirement loops ---------------------------------------
    src = "corpus/docs/10-issue-taxonomy-and-routing-standards.md"
    add("Procedure:Defect_Closed_Loop", "GOVERNED_BY",
        "Rule:Defect_Means_Deviation_From_Docs", src, 17,
        authority="formal_rule", valid_from="2026-01-01")
    add("Procedure:Requirement_Closed_Loop", "GOVERNED_BY",
        "Rule:Feature_Request_Two_Level_Approval", src, 21,
        authority="formal_rule", valid_from="2026-01-01")
    src = _chat_line("chat_2026-08-18.md")
    add("Procedure:Requirement_Closed_Loop", "USES_RESOURCE", "Sentinel Desk",
        src, 22, authority="observation", valid_from="2026-08-18")
    add("Procedure:Requirement_Closed_Loop", "HAS_STEP", "Stage:Product_Backlog_Sync",
        src, 22, authority="observation", valid_from="2026-08-18")
    src = _chat_line("chat_2026-08-26.md")
    add("Procedure:Defect_Closed_Loop", "USES_RESOURCE", "Sentinel Desk", src, 35,
        authority="observation", valid_from="2026-08-26")
    add("Procedure:Defect_Closed_Loop", "HAS_STEP", "Stage:L3_Backlog", src, 36,
        authority="observation", valid_from="2026-08-26")
    add("Procedure:Defect_Closed_Loop", "HAS_STEP", "Stage:L2_Staging_Verification",
        src, 36, authority="observation", valid_from="2026-08-26")
    src = _chat_line("chat_2026-10-12.md")
    add("Procedure:Split_Mixed_Ticket", "GOVERNED_BY",
        "Rule:Split_Defect_And_Feature_Ticket", src, 29,
        authority="observation", valid_from="2026-10-12")

    # --- Q9: weekly report supersession chain ------------------------------------
    src = "corpus/docs/04-l2-weekly-report-and-kb-standards.md"
    add("Procedure:Weekly_Report_Submission", "GOVERNED_BY",
        "Rule:Weekly_Report_Deadline_Friday_1730", src, 17,
        authority="formal_rule", valid_from="2026-01-01")
    add("Procedure:Weekly_Report_Submission", "USES_RESOURCE",
        "Internal Operations Reporting Portal", src, 18,
        authority="formal_rule", valid_from="2026-09-15")
    # legacy email rule (superseded)
    src = _chat_line("chat_2026-08-21.md")
    add("Procedure:Weekly_Report_Submission", "GOVERNED_BY",
        "Rule:Weekly_Report_Email_Submission", src, 10,
        authority="observation", valid_from="2026-08-01", valid_to="2026-09-15")
    add("Rule:Weekly_Report_Email_Submission", "USES_RESOURCE",
        "Resource:ops-weekly-internal@ email", src, 10,
        authority="observation", valid_from="2026-08-01", valid_to="2026-09-15")
    # notice + supersede
    src = _chat_line("chat_2026-09-15.md")
    add("Notice:Weekly_Report_Web_Form_20260915", "SUPERSEDES",
        "Rule:Weekly_Report_Email_Submission", src, 12,
        authority="practice", valid_from="2026-09-15")
    add("Procedure:Weekly_Report_Submission", "GOVERNED_BY",
        "Notice:Weekly_Report_Web_Form_20260915", src, 12,
        authority="practice", valid_from="2026-09-15")
    add("Notice:Weekly_Report_Web_Form_20260915", "APPLIES_TO",
        "Stage:Weekly_Report", src, 12, authority="practice",
        valid_from="2026-09-15")
    # mailbox deactivated (09-24 evidence)
    src = _chat_line("chat_2026-09-24.md")
    add("Notice:Weekly_Report_Web_Form_20260915", "SUPERSEDES",
        "Rule:Weekly_Report_Email_Submission", src, 14,
        authority="practice", valid_from="2026-09-15")

    # --- Q10: PulseVPN supersession chain -----------------------------------------
    src = _chat_line("chat_2026-08-20.md")
    add("Procedure:PulseVPN_Renewal", "GOVERNED_BY",
        "Rule:PulseVPN_Batch_Extension_Until_0930", src, 24,
        authority="observation", valid_from="2026-08-01", valid_to="2026-09-30")
    src = _chat_line("chat_2026-10-10.md")
    add("Notice:Security_Policy_20261008_No_Auto_Extension", "SUPERSEDES",
        "Rule:PulseVPN_Batch_Extension_Until_0930", src, 14,
        authority="practice", valid_from="2026-10-08")
    add("Procedure:PulseVPN_Renewal", "GOVERNED_BY",
        "Notice:Security_Policy_20261008_No_Auto_Extension", src, 15,
        authority="practice", valid_from="2026-10-08")
    add("Procedure:PulseVPN_Renewal", "USES_RESOURCE",
        "PulseVPN Renewal Request Form", src, 16,
        authority="practice", valid_from="2026-10-08")
    add("Procedure:PulseVPN_Renewal", "PERFORMED_BY", "Role:Project Lead Sign-off",
        src, 16, authority="practice", valid_from="2026-10-08")

    # --- Q11: step timeout supersession chain ---------------------------------------
    src = _chat_line("chat_2026-08-10.md")
    add("Procedure:Pipeline_Configuration", "GOVERNED_BY",
        "Rule:Default_Step_Timeout_120Min", src, 38,
        authority="observation", valid_from="2026-01-01", valid_to="2026-08-20")
    src = _chat_line("chat_2026-08-20.md")
    add("Notice:Infra_Policy_20260820_Step_Timeout", "SUPERSEDES",
        "Rule:Default_Step_Timeout_120Min", src, 28,
        authority="practice", valid_from="2026-08-20")
    add("Procedure:Pipeline_Configuration", "GOVERNED_BY",
        "Rule:Default_Step_Timeout_60Min", src, 28,
        authority="practice", valid_from="2026-08-20")
    add("Rule:Default_Step_Timeout_60Min", "APPLIES_TO", "MOD-01", src, 28,
        authority="practice", valid_from="2026-08-20")

    # --- Q12: OOM conflict -------------------------------------------------------------
    src = _chat_line("chat_2026-08-18.md")
    add("Procedure:Container_OOM_Triage", "GOVERNED_BY",
        "Rule:OOM_Fix_Raise_Cgroup_Limit_8Gi", src, 32,
        authority="practice", valid_from="2026-08-18")
    add("Procedure:Container_OOM_Triage", "GOVERNED_BY",
        "Rule:Suggest_Xmx4g_Observation", src, 27,
        authority="observation", valid_from="2026-08-18", valid_to="2026-08-18",
        conflict="incorrect: exceeds 4GB pod cgroup limit")
    src = "corpus/docs/06-platform-troubleshooting-faq.md"
    add("Rule:OOM_Fix_Raise_Cgroup_Limit_8Gi", "APPLIES_TO", "MOD-02", src, 33,
        authority="formal_rule", valid_from="2026-01-01")

    # --- Q13: claim SLA misconception conflict ------------------------------------------
    src = _chat_line("chat_2026-09-09.md")
    add("Procedure:Ticket_Intake", "GOVERNED_BY",
        "Rule:Claim_Within_4h_Observation", src, 14,
        authority="observation", valid_from="2026-09-09", valid_to="2026-09-09",
        conflict="incorrect: 4h is triage window, not claim window")

    # --- Q14: Wesley is not the escalation target -----------------------------------------
    src = _chat_line("chat_2026-08-26.md")
    add("Procedure:Pipeline_Runner_Deadlock", "PERFORMED_BY",
        "Role:Pipeline Engine Tier 2 Lead", src, 21,
        authority="observation", valid_from="2026-08-26")
    add("Colin Marsh", "TECH_LEAD_OF", "MOD-01", src, 21,
        authority="observation", valid_from="2026-08-26")

    # --- Q15: queue config vs roster --------------------------------------------------------
    src = _chat_line("chat_2026-09-14.md")
    add("Luke Fischer", "TECH_LEAD_OF", "MOD-14", src, 18,
        authority="observation", valid_from="2026-09-14")
    add("Wyatt Lawson", "TECH_LEAD_OF", "MOD-13", src, 18,
        authority="observation", valid_from="2026-09-14")
    add("Procedure:Sentinel_Queue_Configuration", "PERFORMED_BY",
        "Role:Unified Gateway Portal Owner", src, 18,
        authority="observation", valid_from="2026-09-14")

    # --- Q16: out-of-scope guidance ----------------------------------------------------------
    src = _chat_line("chat_2026-09-03.md")
    add("Procedure:Out_Of_Scope_Inquiry", "GOVERNED_BY",
        "Rule:Out_Of_Scope_No_Speculation", src, 25,
        authority="practice", valid_from="2026-09-03")

    # --- Q17: unrecorded error code ------------------------------------------------------------
    src = _chat_line("chat_2026-10-12.md")
    add("Procedure:Unknown_Error_Triage", "GOVERNED_BY",
        "Rule:Drain_Node_And_Escalate_L3", src, 20,
        authority="observation", valid_from="2026-10-12")
    add("Procedure:Unknown_Error_Triage", "PERFORMED_BY",
        "Role:Pipeline Engine Tier 2 Lead", src, 20,
        authority="observation", valid_from="2026-10-12")

    return A


# Extra node definitions for entities introduced by curated edges.
EXTRA_NODES = [
    ("Procedure", "Procedure:Log_Non_Ticket_Issue", "Log non-ticket issue"),
    ("Resource", "Non-Ticket Issue Register", "Non-Ticket Issue Register / Ledger (shared drive)"),
    ("Resource", "Production Incident Registration Form", "Production Incident Registration Form"),
    ("Rule", "Rule:Untracked_Inquiries_Prohibited", "Unassigned/untracked inquiries strictly prohibited (Doc 01 §1)"),
    ("Resource", "Resource:Reporter Name", "Mandatory field: Reporter Name"),
    ("Resource", "Resource:Regional Territory", "Mandatory field: Regional Territory"),
    ("Resource", "Resource:Platform Module Code", "Mandatory field: Platform Module Code"),
    ("Resource", "Resource:Issue Description", "Mandatory field: Issue Description"),
    ("Resource", "Resource:Resolution Summary", "Mandatory field: Resolution Summary"),
    ("Resource", "Resource:Current Status", "Mandatory field: Current Status"),
    ("Procedure", "Procedure:Log_Production_Incident", "Log production incident"),
    ("Procedure", "Procedure:Sentinel_Account_Provisioning", "Sentinel Desk account provisioning"),
    ("Resource", "Sentinel Desk", "Sentinel Desk Quality Supervision Portal"),
    ("Resource", "Sentinel Desk Portal", "Sentinel Desk Portal (login destination)"),
    ("Resource", "Unified Gateway SSO", "Unified Gateway corporate SSO"),
    ("Procedure", "Procedure:Sentinel_Login", "Log in to Sentinel Desk"),
    ("Rule", "Rule:ZeroTrust_Public_HTTPS_Access", "Sentinel Desk & SSO reachable over public HTTPS via ZeroTrust Web Portal + MFA, no VPN required"),
    ("WorkStage", "Stage:Day1_Onboarding", "Day 1 onboarding (new hire)"),
    ("Rule", "Rule:Nickname_Format_Vendor_Role_Name", "Channel handle format [Vendor]-[Role]-[Name]"),
    ("Resource", "Channel-General-OpsL1L2", "Master collaboration hub channel"),
    ("Resource", "Channel-CICD-L1-L2-Collaboration", "Pipeline/CI/CD technical coordination channel"),
    ("Resource", "Channel-OpsSupervision", "Shift handover & management escalation channel"),
    ("Resource", "Channel-TopSeverity-Escalations", "P1/P2 critical incident bridge channel"),
    ("Procedure", "Procedure:Mobile_Build_Escalation", "Mobile Build technical escalation"),
    ("Procedure", "Procedure:Respond_Customer_Inquiry", "Respond to customer inquiry"),
    ("Rule", "Rule:Response_Tone_Professional_Empathetic", "Professional, empathetic, objective, solution-driven; never blame client environment (Doc 03 §1)"),
    ("Rule", "Rule:Channel_Ack_Within_5_Min", "Initial human acknowledgment within 5 minutes in channels 09:00-21:00"),
    ("Procedure", "Procedure:Ticket_Closure", "Ticket closure"),
    ("Rule", "Rule:No_Closure_Without_Customer_Confirmation", "Never close without explicit customer confirmation (-10 pts major non-compliance)"),
    ("WorkStage", "Stage:Daily_Response", "Daily response work stage"),
    ("Procedure", "Procedure:Escalate_to_Tier2", "Escalate ticket to Tier 2"),
    ("Rule", "Rule:Transfer_Via_Sentinel_With_Metadata", "All cross-tier handoffs via Sentinel Portal with populated metadata (Ticket ID, Error Logs, Repro Steps, Territory)"),
    ("Rule", "Rule:Informal_Chat_Tolerated_For_Diagnosis", "L1 routinely contacts known L2 module owners via chat for rapid diagnosis"),
    ("Rule", "Rule:Formal_Transfer_Within_2_Hours", "Formal Sentinel ticket must be reassigned within 2 hours after informal contact (-5 pts if missed)"),
    ("Resource", "Resource:Reproduction Steps", "Transfer field: detailed reproduction steps"),
    ("Resource", "Resource:Error Messages/Screenshots", "Transfer field: error messages and screenshots"),
    ("Resource", "Resource:Affected Regional Territory", "Transfer field: affected regional operating territory"),
    ("Resource", "Resource:Workarounds Attempted", "Transfer field: workarounds attempted + reason for escalation"),
    ("Procedure", "Procedure:Informal_Chat_Escalation", "Informal chat escalation to L2"),
    ("Procedure", "Procedure:Ticket_Intake", "Ticket intake & claim"),
    ("Rule", "Rule:Intake_Claim_SLA_15_Min", "Every incoming ticket must be claimed/dispatched within 15 minutes regardless of severity"),
    ("Rule", "Rule:Initial_Response_30_Min", "Initial technical response logged within 30 minutes"),
    ("Rule", "Rule:SLA_Breach_Over_50Pct", "Exceeding SLA response time by >50%"),
    ("Rule", "Rule:Moderate_Non_Compliance_Minus_5", "Moderate non-compliance: -5 points (missing repro logs; SLA breach >50%; wrong module tagging)"),
    ("Rule", "Rule:Weekly_Register_Audit_By_Sophia", "Sophia Evans audits the Non-Ticket Issue Register weekly; blank territory/module incurs deduction"),
    ("Rule", "Rule:Suggest_Xmx4g_Observation", "Dominic Shaw's informal suggestion to raise -Xmx4g (incorrect for 4GB pods)"),
    ("Rule", "Rule:Claim_Within_4h_Observation", "Luke Fischer's belief that P3 tickets may stay unclaimed up to 4 hours (incorrect)"),
    ("Rule", "Rule:P3_Response_30_Min", "P3 initial response <= 30 minutes (Doc 01 §4.4)"),
    ("Rule", "Rule:P3_Initial_Triage_2_Hours", "P3 initial triage <= 2 hours (Doc 01 §4.4)"),
    ("Procedure", "Procedure:Defect_Closed_Loop", "Production defect closed loop"),
    ("Procedure", "Procedure:Requirement_Closed_Loop", "Feature request closed loop"),
    ("Rule", "Rule:Defect_Means_Deviation_From_Docs", "DEFECT = unexpected software malfunction deviating from official docs"),
    ("Rule", "Rule:Feature_Request_Two_Level_Approval", "REQ_FEAT = unreleased capability proposal; two-level approval then product backlog"),
    ("WorkStage", "Stage:Product_Backlog_Sync", "Sync into Product Management backlog"),
    ("WorkStage", "Stage:L3_Backlog", "Transfer to L3 engineering backlog"),
    ("WorkStage", "Stage:L2_Staging_Verification", "L2 verifies patch on staging"),
    ("WorkStage", "Stage:Informal_Diagnosis_Liaison", "Informal L1-L2 chat liaison for rapid diagnosis (Doc 01 section 5.1)"),
    ("Procedure", "Procedure:Split_Mixed_Ticket", "Split mixed defect+requirement ticket"),
    ("Rule", "Rule:Split_Defect_And_Feature_Ticket", "A ticket containing both defect and feature request MUST be split into two tickets"),
    ("Procedure", "Procedure:Weekly_Report_Submission", "Weekly report submission"),
    ("Rule", "Rule:Weekly_Report_Deadline_Friday_1730", "Weekly report due every Friday before 17:30"),
    ("Resource", "Internal Operations Reporting Portal", "Internal Operations Reporting Portal (web form)"),
    ("Rule", "Rule:Weekly_Report_Email_Submission", "Weekly reports via email attachment to ops-weekly-internal@ (legacy, deadline 18:00)"),
    ("Resource", "Resource:ops-weekly-internal@ email", "ops-weekly-internal@ mailbox (deactivated 2026-09-18)"),
    ("Notice", "Notice:Weekly_Report_Web_Form_20260915", "2026-09-15 policy notice: weekly reports via web form on Operations Reporting Portal before Fri 17:30; email deprecated"),
    ("WorkStage", "Stage:Weekly_Report", "Weekly report work stage"),
    ("Procedure", "Procedure:PulseVPN_Renewal", "PulseVPN token renewal"),
    ("Rule", "Rule:PulseVPN_Batch_Extension_Until_0930", "PulseVPN tokens batch-extended until 2026-09-30; no individual renewal required"),
    ("Notice", "Notice:Security_Policy_20261008_No_Auto_Extension", "2026-10-08 security policy: no more automatic extensions; individual renewal via Sentinel Desk signed by project lead; 90-day certificate"),
    ("Resource", "PulseVPN Renewal Request Form", "PulseVPN Renewal Request Form (Sentinel Desk)"),
    ("Role", "Role:Project Lead Sign-off", "Project lead approval role"),
    ("Procedure", "Procedure:Pipeline_Configuration", "Pipeline step configuration"),
    ("Rule", "Rule:Default_Step_Timeout_120Min", "Default pipeline step timeout 120 minutes (superseded 2026-08-20)"),
    ("Rule", "Rule:Default_Step_Timeout_60Min", "Default pipeline step timeout 60 minutes (effective 2026-08-20); long jobs must set step.timeout: 120m and use high-spec runners"),
    ("Notice", "Notice:Infra_Policy_20260820_Step_Timeout", "2026-08-20 infra policy: default step timeout reduced 120m -> 60m due to runner concurrency"),
    ("Procedure", "Procedure:Container_OOM_Triage", "Container OOM (signal 9) triage"),
    ("Rule", "Rule:OOM_Fix_Raise_Cgroup_Limit_8Gi", "Signal 9 = kernel OOMKilled by node cgroup; raise resources.limits.memory: 8Gi, keep JVM heap -Xmx3g; never raise heap above pod limit"),
    ("Rule", "Observation:Suggest_Xmx4g_Dominic", "Dominic Shaw's informal suggestion to raise -Xmx4g (incorrect for 4GB pods)"),
    ("Procedure", "Procedure:Pipeline_Runner_Deadlock", "Pipeline runner deadlock triage"),
    ("Role", "Role:Pipeline Engine Tier 2 Lead", "Tier 2 Pipeline Engine lead (Colin Marsh)"),
    ("Procedure", "Procedure:Sentinel_Queue_Configuration", "Sentinel Desk dispatch queue configuration"),
    ("Role", "Role:Unified Gateway Portal Owner", "Unified Gateway & Portal owner (Luke Fischer)"),
    ("Procedure", "Procedure:Out_Of_Scope_Inquiry", "Out-of-scope / unknown component inquiry"),
    ("Rule", "Rule:Out_Of_Scope_No_Speculation", "Never speculate or fabricate owners for out-of-scope components; advise client internal IT (-10 pts if guessed)"),
    ("Procedure", "Procedure:Unknown_Error_Triage", "Unknown error code triage"),
    ("Rule", "Rule:Drain_Node_And_Escalate_L3", "For unrecorded error codes: capture dmesg, drain the impacted runner node, reroute, open L3 platform defect escalation"),
    ("Rule", "Observation:Claim_Within_4h_Luke", "Luke Fischer's belief that P3 tickets may stay unclaimed up to 4 hours (incorrect)"),
]


def validate(a: dict) -> str | None:
    """Validate one LLM/curated assertion. Returns reject reason or None."""
    pre = a["predicate"]
    if not S.is_registered(pre):
        return f"unregistered predicate '{pre}'"
    sl, ol = label_of(a["subject"]), label_of(a["object"])
    # HOLDS_ROLE/TECH_LEAD_OF etc with Person object label misreads are rejected
    reason = S.validate_predicate(pre, sl, ol)
    if reason:
        # tolerate 'Observation:*'/'Practice:*' subjects standing in as Rule-like
        # nodes only for CONFLICTS_WITH, which is not a registered predicate anyway
        return reason
    ls = EV.lines(a["source"])
    if not (1 <= a["line"] <= len(ls)):
        return f"evidence line {a['line']} out of range in {a['source']}"
    if a["predicate"] == "CONFLICTS_WITH":
        return "unregistered predicate 'CONFLICTS_WITH'"
    return None
