# ERP infrastructure (PostgreSQL + n8n)

Owner: Person 1 (`scm-erp-infra`). Files: `docker-compose.yml`, `db/init.sql`, `n8n/supply_chain_workflow.json`, `tests/test_erp.py`.

## Start / reset / check
```bash
docker compose up -d                 # starts erp-db (Postgres 16) and n8n
docker compose down -v && docker compose up -d   # RESET (init.sql only runs on a fresh volume)
docker exec -it erp-db psql -U scm -d erp -c "SELECT part_id, days_of_cover FROM v_part_risk ORDER BY part_id;"
```
Expected: 7 rows: P-1001 12.0, P-1002 21.0, P-2001 18.0, P-3001 35.0, P-3002 8.0, P-4001 40.0, P-5001 25.0.

Database: host `localhost`, port `5432`, db `erp`, user `scm`, password `scm`.
`DATABASE_URL=postgresql+psycopg2://scm:scm@localhost:5432/erp`

Note: `down -v` also wipes n8n's volume, so re-import and re-activate the workflow after a reset.

## n8n
1. Open http://localhost:5678 and create the owner account (local only).
2. Workflows → ⋯ → **Import from File** → `n8n/supply_chain_workflow.json` (3 nodes: Webhook → Generate RFQs & Plan → Respond to Webhook).
3. **Activate** the workflow (toggle, top right). Only active workflows answer on `/webhook/...`; `/webhook-test/...` works only while listening for a test event.
4. Webhook URL: `http://localhost:5678/webhook/supply-chain-rfq` (`N8N_WEBHOOK_URL`).

CLI alternative (no browser): `docker cp n8n/supply_chain_workflow.json n8n:/tmp/wf.json && docker exec n8n n8n import:workflow --input=/tmp/wf.json && docker exec n8n n8n publish:workflow --id=scmrfqworkflow0001 && docker restart n8n`

## curl test
Use the payload from Appendix 3 of the brief saved as `sample_payload.json`:
```bash
curl -X POST http://localhost:5678/webhook/supply-chain-rfq -H "Content-Type: application/json" -d @sample_payload.json
```
Expect `"status":"RECEIVED"`, `"rfqs_generated":2`, `rfq_documents` (subject + body per supplier) and `"routed_for_approval_to":"Chief Financial Officer"`. On Windows use `curl.exe`.

## Automated tests
`pip install psycopg2-binary pytest python-dotenv`, then `python -m pytest -q` from the repo root (7 tests; the DB must be running).

## Lane filter note
P-5001 (cover 25, China/Yantian) and P-4001 (cover 40, domestic) are out of scope for a Taiwan-only disruption even though 25 < 26. Apply the lane filter (supplier country / export port) **before** the SOP 3.2 trigger test.

## Part B+ notes

**1. Single-source risk (P-4001 / S302).** Left as-is: a part with exactly one `supplier_parts` row (role `PRIMARY`) and no `APPROVED_BACKUP` is single-source, and the Analyst can detect that with a plain join (no backup row = no eligible RFQ target), so no extra column was added. The Coordinator should then fall back to SOP §4 contingency steps (air freight, re-route, safety stock) and the contract's force-majeure terms rather than an RFQ. `test_p4001_stays_single_source` fails if anyone adds a backup for P-4001.

**2. Shared capacity blind spot.** `monthly_capacity` is per (supplier, part). S301 backs P-1001, P-3001 and P-3002 with 12,000 / 30,000 / 15,000 per month, but those lines presumably share one plant. In the demo only P-3002 RFQs S301 (26,000 vs 15,000, an intentional breach), so P-1001 going to S201 hides the problem; if P-1001 also fell to S301 the combined demand would exceed any single-line figure and the per-part numbers would overstate availability. Proposal (not implemented): add `suppliers.total_monthly_capacity` (or a `supplier_capacity` table) and make the Analyst check the **sum** of RFQ quantities per supplier against it, keeping per-part capacity as a secondary limit.

**3. n8n extension investigated: audit log (SOP §6.3, 7-year retention).** Not enabled for the demo. Best fit is the n8n Postgres node inserting each payload into an `rfq_audit_log(event_id, payload JSONB, processed_at)` table, using an n8n credential (host `erp-db`, user `scm`) stored in n8n's encrypted credential store, never in the exported JSON. It was left out because the exported workflow would then depend on a credential that every teammate must recreate after import, and it adds a failure point to the live demo. Real email (SMTP/Gmail) was rejected for the same reason plus the risk of sending mail to the `.example` supplier addresses.


## Re-audit changes (2026-10-03)
- One PRIMARY per part is enforced (partial unique index); `purchase_orders` supplier/part are NOT NULL with a composite FK to `supplier_parts`, a price snapshot, and `po_value = qty x price` CHECK.
- Lane is structured (`origin_port`, `dest_port`, `transport_mode`); `shipping_route` is a generated column, so `LIKE 'Kaohsiung%'` still works. Road suppliers have `export_port` NULL (views show `Domestic (road)`).
- `v_part_risk` keeps 4 decimals, exposes `data_age_days`, and LEFT JOINs the primary supplier. `v_eligible_backups` flags S402 (`rfq_allowed_without_signoff = false`).
- New `action_log` table (audit, SOP 6.3) plus roles `scm_ro` (read-only, 5 s timeout) and `scm_audit` (insert into `action_log` only).
- Images pinned: `postgres:16.4`, `n8nio/n8n:2.41.6`. Ports are bound to 127.0.0.1.
- Limitation kept: inventory is a snapshot (no on-order quantity, no lead-time-vs-cover logic), and `monthly_capacity` is per (supplier, part).


## Round 3 changes (2026-10-03)
- `db/init.sql` is the frozen V001 baseline; all later changes are versioned migrations in `db/migrations/` applied by `python -m tools.migrate` (checksum-verified, advisory-locked). Fresh containers get V002 through the compose mount.
- V002 adds `ports` (foreign keys from suppliers and purchase orders), the `action_log` state machine, the `dead_letter` queue (`dlq_claim`, `dlq_release`), and the views `v_part_pipeline` and `v_lane_exposure`.
- n8n workflows are generated from `n8n/js/*.js` by `n8n/build_workflows.py`; `scripts/setup_n8n.py` installs them with credentials and header auth.
