"""Generate docs/RECOVERY_BLUEPRINT.md: sequence table, old-vs-new per component, and the complete final content of every
rebuilt file, read from disk (so the document can never drift from the code). Run: python scripts/build_blueprint.py"""
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

SEQUENCE = """| Layer | What it contains | Why it must be written before the next layer |
|---|---|---|
| 1 Bedrock | `db/init.sql` (V001 baseline), `db/migrations/V002`, `db/migrations/V003` (approval ladder, escalation clock, notifications), `tools/migrate.py`, `tools/db.py` | Every other layer reads or writes these tables and roles. n8n Postgres nodes, the dead-letter queue and the guardrails cannot exist without `ports`, `action_log` lifecycle, `dead_letter`, `dlq_claim` and the pooled engines. |
| 2 Ingress guard | `schemas/policy_constants.py`, `schemas/ingress.py`, `schemas/guardrails.py`, `schemas/policy_check.py` (clauses 4.5 / 4.7 derived from the ERP), `schemas/payload.py`, `tools/search_tool.py` (URL registry), `tools/preflight.py` | Orchestration calls these validators, so they must exist first. They also need the Layer 1 tables (ERP rows are validated on load) and are the single source of the numbers that n8n and the KB are checked against. |
| 3 Orchestration | `n8n/js/*.js`, `n8n/build_workflows.py`, the four workflow JSON files (intake, approval decision, escalation tick, error handler), `tools/approvals.py`, `scripts/setup_n8n.py`, `tools/dlq.py`, `tools/n8n_tool.py`, `main.py`, `docker-compose.yml` | The workflow writes to Layer 1 tables and applies Layer 2 rules; the tool and the n8n workflow share one state machine. Wiring (`main.py`) comes last because it calls everything beneath it. |
| 4 Intelligence | `knowledge_base/source/build_pdfs.py` (SOP-SC-014, contracts and the new SOP-SC-015, + `clauses.jsonl`), `tools/rag_tool.py`, `tools/kb_lint.py` | Retrieval validates citations against the Layer 2 payload model and lints the KB against Layer 2 constants and Layer 1 ERP ids, so it can only be finished once those are fixed. |
| 5 Verification | `scripts/bootstrap.sh`, `scripts/chaos_check.py` (26 live checks), about 200 tests | Proves layers 1-4 together on a clean volume. |"""

OLD_NEW = [
 ("db/migrations/V002__lifecycle_dlq_ports.sql", "A one-shot `init.sql`; free-text ports; `action_log` was a plain table; failures lived in an `outbox/` folder.",
  "Versioned, idempotent migration: `ports` reference table with foreign keys, `action_log` state machine enforced by triggers (immutable payload, append-only, legal transitions only), `dead_letter` queue with `SKIP LOCKED` claim and exponential backoff, pipeline views, least-privilege grants."),
 ("tools/migrate.py", "Schema changes required `docker compose down -v` and lost data.",
  "Advisory-locked runner with per-file transactions and checksum verification; records the V001 baseline for databases created from `init.sql`; safe to run from several processes."),
 ("tools/db.py", "A new connection per audit write; default pool for reads; a database restart broke the `--watch` loop.",
  "One pooled engine per role (`ro`, `audit`, `migrate`) with `pool_pre_ping`, recycling, timeouts and session guards."),
 ("schemas/policy_constants.py", "25 lakh, 1 crore, 1.2, +5, 30%, 15% duplicated in Python, n8n JavaScript and the PDFs.",
  "One module; `kb_lint` and the JS drift test fail when any copy disagrees."),
 ("schemas/ingress.py + schemas/guardrails.py", "Scout and Analyst free text flowed unchecked to the Coordinator.",
  "Typed parsers and deterministic checks (URLs must come from real search results, two independent domains confirm an incident, every Analyst number recomputed from the ERP, lane filter, size caps). CrewAI guardrails send precise feedback to the agent and retry."),
 ("schemas/policy_check.py", "Shape-only validation of the final payload.",
  "Approver, severity, supplier eligibility, MOQ, price, quantity formula, recipient from the ERP, and clause-level citation checks."),
 ("tools/preflight.py", "No runtime data gate.", "Structural ERP checks block the crew; freshness warns (or blocks with `ERP_STRICT_FRESHNESS`)."),
 ("n8n/js/validate_build.js + both workflow files", "Code node filled gaps with invented defaults, trusted LLM totals, had no auth, dedupe by racy static data, failures vanished.",
  "Header-auth webhook, validate-only Code node that never throws, database claim (`INSERT ... ON CONFLICT` / `UPDATE ... RETURNING`) for race-free idempotency, HTTP 422 plus a `dead_letter` row for invalid input, error sub-workflow that records crashes."),
 ("scripts/setup_n8n.py", "Manual UI clicks (import, activate, restart).", "Credentials, both workflows, publish, restart and a readiness wait, all headless; the token is generated into `.env`."),
 ("tools/dlq.py + tools/n8n_tool.py", "Retry loop then a file in `outbox/`; replay could double-send; no lifecycle.",
  "Intent recorded before sending, states PENDING/QUEUED/DELIVERED/REJECTED/DEAD, backoff and dead-lettering, permanent 4xx detection, spill file only when the database itself is down, `--replay-dlq` and automatic replay in `--watch`."),
 ("main.py", "Single unguarded cycle; no migrations; no replay.", "Migrations, integrity gate, KB lint, guardrails, spend bounds, self-healing watch loop, `--scout-report` replay."),
 ("knowledge_base/source/build_pdfs.py + tools/rag_tool.py", "pypdf re-parse flattened tables, overlap stripped clause numbers, citations checked by page only.",
  "Structured `clauses.jsonl` emitted by the same flow as the PDF, clause-atomic index, relative-distance cutoff, table companion rule, clause-level citation tracking."),
 ("db/migrations/V003__approval_escalation.sql", "The system never waited for the SOP section 5 approver: n8n returned RFQ documents at once and nobody decided anything; a silent approver could not stall or escalate anything because no state existed.",
  "`approvals` state machine (PENDING / ESCALATED / APPROVED / REJECTED / HELD), opened by a trigger on every RFQ action, deadlines from `approval_policy` (L2 8 h, L3 4 h), `approval_tick(p_now)` for reminder at 50%, escalation one level up with a fresh window, HOLD at the top (never an automatic approval), `approval_decide` that never lowers authority, write-once documents released only after APPROVED, append-only `approval_events`, `notification_outbox` with a SKIP LOCKED claim."),
 ("n8n/approval_decision_workflow.json + approval_tick_workflow.json", "No way to decide, and no clock.",
  "Token-protected `/webhook/supply-chain-approval` (200 / 403 / 404 / 409 / 422 mapped from the database outcome, invalid requests dead-lettered) and a 5-minute schedule plus protected manual webhook that runs the escalation clock and dispatches notifications."),
 ("schemas/policy_check.py (derive)", "Clauses 4.5 and 4.7 existed only as text the LLM had to remember.",
  "`derive` computes `stockout_gaps` (4.7) and `production_notifications` (4.5) from the ERP; the LLM never supplies them; the database turns them into Production Planning notices."),
 ("tools/approvals.py", "No operator interface.", "CLI and functions: status, approve, reject, tick, inbox, all through the same database functions as the n8n webhook."),
 ("knowledge_base SOP-SC-015", "No written rule for what happens when an approver is silent.",
  "One-page SOP generated from `policy_constants`, so the PDF, the database seed and the n8n logic cannot disagree; `kb_lint` enforces it."),
 ("n8n tick + error workflows (Gmail)", "Notifications existed only as database rows and execution logs.",
  "Routed email through the SCM Gmail credential: `notification_routes` maps each recipient to an address (none seeded), one email per message, failures kept for retry and abandoned after 5 attempts, unrouted messages logged only; n8n failures email the `SCM Alerts` route. All Code-node logic is unit-tested under Node (`tests/test_n8n_code_nodes.py`)."),
 ("tools/kb_lint.py", "Nothing detected a contradicting or dangling clause.", "Unique ids, resolvable references, ERP ids, numeric facts equal to the constants."),
]

FILES = [
 ("db/init.sql", "sql"), ("db/migrations/V002__lifecycle_dlq_ports.sql", "sql"), ("tools/migrate.py", "python"), ("tools/db.py", "python"),
 ("schemas/policy_constants.py", "python"), ("schemas/ingress.py", "python"), ("schemas/guardrails.py", "python"),
 ("schemas/policy_check.py", "python"), ("schemas/payload.py", "python"), ("tools/search_tool.py", "python"), ("tools/preflight.py", "python"),
 ("db/migrations/V003__approval_escalation.sql", "sql"), ("db/migrations/V004__notification_routes.sql", "sql"), ("db/migrations/V005__route_set_trims_first.sql", "sql"),
 ("n8n/js/validate_build.js", "javascript"), ("n8n/js/build_documents.js", "javascript"), ("n8n/js/compose_response.js", "javascript"),
 ("n8n/js/duplicate_response.js", "javascript"), ("n8n/js/rejected_response.js", "javascript"), ("n8n/js/format_error.js", "javascript"),
 ("n8n/js/validate_decision.js", "javascript"), ("n8n/js/shape_decision.js", "javascript"), ("n8n/js/rejected_decision.js", "javascript"),
 ("n8n/js/summarize_tick.js", "javascript"), ("n8n/js/format_notifications.js", "javascript"), ("n8n/js/split_messages.js", "javascript"),
 ("n8n/js/collect_results.js", "javascript"), ("n8n/js/report_tick.js", "javascript"), ("n8n/js/build_alert_mail.js", "javascript"),
 ("n8n/build_workflows.py", "python"), ("n8n/supply_chain_workflow.json", "json"), ("n8n/approval_decision_workflow.json", "json"),
 ("n8n/approval_tick_workflow.json", "json"), ("n8n/error_handler_workflow.json", "json"), ("scripts/setup_n8n.py", "python"), ("tools/approvals.py", "python"),
 ("tools/dlq.py", "python"), ("tools/n8n_tool.py", "python"), ("main.py", "python"), ("docker-compose.yml", "yaml"),
 ("scripts/bootstrap.sh", "bash"), ("scripts/_compose.py", "python"), ("docs/SETUP_GUIDE.md", "markdown"), ("scripts/chaos_check.py", "python"),
 ("knowledge_base/source/build_pdfs.py", "python"), ("knowledge_base/source/build_docx.py", "python"),
 ("tools/rag_tool.py", "python"), ("tools/kb_lint.py", "python"), ("knowledge_base/clauses.jsonl", "json"),
]

BOOT = """## Bring-up and verification (one command)
```bash
bash scripts/bootstrap.sh          # venv, containers, migrations, n8n setup, KB lint, dry-run, full test suite
python scripts/chaos_check.py      # live: auth, 20 parallel duplicates, n8n down + replay, wrong token, killed DB connections
python main.py --scout-report samples/scout_typhoon.md   # deterministic demo scenario through the real Analyst and Coordinator
```
The dead-letter flow in one picture:
```
Coordinator -> trigger_n8n -> policy_check -> action_log PENDING -> POST n8n (token)
   200 RECEIVED / DUPLICATE ......................... action_log DELIVERED
   422 (payload fault) .............................. action_log REJECTED   + n8n writes dead_letter(source='n8n-validate', retryable=false)
   timeout / 5xx / 401 / 403 / 429 .................. action_log QUEUED     + dead_letter(source='tool-delivery', retryable=true)
        dlq_claim (SKIP LOCKED) -> re-POST -> dlq_release: RESOLVED | OPEN (2^n min backoff, max 6 h) | DEAD after 6 attempts
   database also down ............................... outbox/<event>.json  -> adopted into the queue when the database returns
n8n node crash -> Error sub-workflow -> dead_letter(source='n8n-error')
```
The approval lifecycle (SOP-SC-015):
```
action accepted (RFQ) -> approvals PENDING at the SOP-SC-014 section 5 level, deadline = now + 8 h (L2) / 4 h (L3), documents HELD
   50% of the window ......... one REMINDER to the current approver
   window ends ............... ESCALATED one level up (Ops -> Head of SCM -> CFO), fresh window, notification to the new level
   top level window ends ..... HELD: nothing released, alert raised; a human may still decide (no automatic approval, no bypass)
   approver (>= required level) decides via /webhook/supply-chain-approval or tools.approvals -> APPROVED releases the documents once / REJECTED
Clauses 4.5 / 4.7: derived from the ERP at intake -> Production Planning notices + air-freight / stock-out-gap lines
```
"""


def main() -> None:
    out = ["# Recovery blueprint: ERP, n8n and knowledge base", "",
           "Generated by `scripts/build_blueprint.py` from the files on disk. Everything below is the code that ships.", "",
           "## Dependency sequence", "", SEQUENCE, "", BOOT, "## Old simplistic logic vs new architecture", ""]
    for path, old, new in OLD_NEW:
        out += [f"### `{path}`", f"- **Old:** {old}", f"- **New:** {new}", ""]
    out += ["## Complete final files", ""]
    for rel, lang in FILES:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            body = f.read().rstrip("\n")
        fence = "````" if "```" in body else "```"
        out += [f"### `{rel}`", "", f"{fence}{lang}", body, fence, ""]
    dest = os.path.join(ROOT, "docs", "RECOVERY_BLUEPRINT.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("wrote", dest, f"({os.path.getsize(dest) // 1024} KB, {len(FILES)} files)")


if __name__ == "__main__":
    main()
