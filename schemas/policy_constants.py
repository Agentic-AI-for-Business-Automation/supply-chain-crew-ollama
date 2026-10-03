"""Single source of truth for every number in SOP-SC-014. policy_check, the preflight gate, the guardrails and the
drift tests (n8n JavaScript, PDF text) all read these values; nothing else may hard-code them."""
LAKH = 100_000
CRORE = 100 * LAKH

OPS_LIMIT_INR = 25 * LAKH            # SOP 5: up to this total -> Operations Manager
HEAD_LIMIT_INR = 1 * CRORE           # SOP 5: up to this total -> Head of SCM, above -> CFO
APPROVER_OPS = "Operations Manager - Procurement"
APPROVER_HEAD = "Head of Supply Chain Management"
APPROVER_CFO = "Chief Financial Officer"
APPROVERS = (APPROVER_OPS, APPROVER_HEAD, APPROVER_CFO)

L1_BELOW_DAYS = 7                    # SOP 2: delay < 7 days is L1
L2_MAX_DAYS = 21                     # SOP 2: 7..21 days is L2, above is L3
BUFFER_DAYS = 5                      # SOP 3.2 / 3.3
QTY_UPLIFT = 1.2                     # SOP 3.3
SAFETY_STOCK_UPLIFT_PCT = 30         # SOP 4.2
AIR_FREIGHT_MAX_PREMIUM_PCT = 15     # SOP 4.1
CONFIRMING_SOURCES = 2               # SOP 2.3: independent sources needed to confirm an event
STOCKOUT_NOTIFY_DAYS = 7             # SOP 4.5
RECORD_RETENTION_YEARS = 7           # SOP 6.3


def approver_for(total_inr: float) -> str:
    if total_inr <= OPS_LIMIT_INR:
        return APPROVER_OPS
    if total_inr <= HEAD_LIMIT_INR:
        return APPROVER_HEAD
    return APPROVER_CFO


def severity_for(delay_days: int) -> str:
    return "L1" if delay_days < L1_BELOW_DAYS else "L2" if delay_days <= L2_MAX_DAYS else "L3"


# ---- SOP-SC-015: approval timeouts and escalation (windows reuse the response windows of SOP-SC-014 section 2) ----
# To change a number here: edit it, run `python -m tools.migrate` (syncs the database), rebuild the PDFs (build_pdfs.py), run the tests.
APPROVAL_WINDOW_HOURS = {"L2": 8, "L3": 4}     # SOP-SC-015 2.1: time each approval level has to decide
APPROVAL_REMINDER_PCT = 50                     # SOP-SC-015 2.2: reminder at this share of the window
APPROVAL_LADDER = APPROVERS                    # SOP-SC-015 2.3: escalation goes one level up this list, in order
