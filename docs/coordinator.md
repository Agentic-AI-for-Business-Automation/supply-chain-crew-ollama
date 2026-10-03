# Coordinator — Integration (P6)

## What it does
- `schemas/payload.py`: Pydantic model for the action record posted to n8n. Field names are a contract with Person 1's n8n Code node. Validators: `est_value_inr ≈ quantity × unit_price_inr` (1% tolerance), `RFQ ⇒ ≥1 rfqs entry and ≥1 source URL`, `MONITOR_ONLY ⇒ empty rfqs`, approver is one of the 3 literals.
- `tools/n8n_tool.py`: `trigger_n8n(payload_json)` strips code fences, validates, enriches (`event_id=SCD-YYYYMMDD-XXXXXX`, `generated_at`, `company`, `origin`), POSTs with 3 retries (2/4/6 s), and on failure writes `outbox/<event_id>.json`. `STATE["sent"]` lets `main.py` warn if nothing was sent. `replay_outbox()` re-sends queued payloads.
- `agents/coordinator.py`: RAG-grounded decision + exact one successful n8n call. Output saved to `reports/action_brief.md`.
- `main.py`: boots (watchlist from ERP, RAG index), runs the 3-agent sequential crew, supports `--focus`, `--watch N`, `--simulate-search-failure`, `--dry-run`, `--no-memory`, `--replay-outbox`.

## Expected run result (A5 demo)
Severity L2; RFQs 22,000/15,000/12,000/26,000 to S201/S201/S202/S301; total ₹5,02,99,000 → Chief Financial Officer; capacity warning for P-3002 (26,000 vs 15,000/month); no RFQ for P-3001; citations with page numbers.

## Part B+ notes
1. **Human-in-the-loop for CFO RFQs:** §5 routes >₹1cr to the CFO. Chosen: keep the crew autopilot for the demo but `approval_authority` is carried in the payload, so an n8n IF node can pause >₹1cr records for approval without changing the agent contract. Tradeoff: auto-send is faster to demo; pausing is safer for real spend.
2. **Outbox operations:** payloads queue as `outbox/<event_id>.json`; `replay_outbox()` re-POSTs and deletes on success (at-least-once; n8n side does not dedupe event_id, so replays may duplicate RFQs — acceptable for a mock workflow, note for production).
3. **Memory cost vs quality:** `memory=True` costs OpenAI embedding calls per run; for single-shot demos `--no-memory` is acceptable; for `--watch` mode memory helps cross-run dedup.
