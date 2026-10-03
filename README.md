# Supply Chain Disruption Monitoring & Mitigation Crew

> **New to this project or setting it up on a new computer? Follow [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md).** One command (`bash scripts/bootstrap.sh`) sets everything up.

**Course:** Agentic AI for Business Automation (AAIBA), Term 04, PGDM-BDA, 2026-27 · **Topic 5**

A three-agent CrewAI system for "Aravalli Mobility Pvt Ltd", a fictional EV two-wheeler maker in Manesar.
It detects supplier-lane disruptions from live web news, measures the impact on ERP inventory and open
purchase orders, decides a mitigation strictly from SOP and vendor-contract clauses, and triggers a
procurement workflow in n8n.

```
Scout (web search) ──► Inventory Impact Analyst (SQL on Postgres ERP) ──► Operations Coordinator (RAG on SOP PDFs ──► n8n webhook)
```

## One-command setup
```bash
bash scripts/bootstrap.sh       # venv, containers, migrations, n8n credentials + workflows, KB lint, dry-run, tests
python scripts/chaos_check.py   # live resilience checks (n8n down, duplicates, wrong token, killed DB connections)
```
Full design, old-vs-new explanation and every rebuilt file: [`docs/RECOVERY_BLUEPRINT.md`](docs/RECOVERY_BLUEPRINT.md).

## Prerequisites
- Python 3.10–3.12 (`crewai==1.15.23` needs `>=3.10,<3.14`), Git, Docker with Compose
- An LLM key (`OPENAI_API_KEY` for `openai/gpt-4o-mini`, or a local Ollama model of about 14B or larger)
- A Serper key (serper.dev, free tier). Without it the Scout falls back to DuckDuckGo.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then fill in the keys
docker compose up -d                                   # erp-db (Postgres 16) + n8n
docker exec erp-db psql -U scm -d erp -c "SELECT part_id, days_of_cover FROM v_part_risk ORDER BY part_id;"   # 7 rows
```
Then run `python scripts/setup_n8n.py`. It imports the credentials (webhook token generated into `.env`, audit database
login), both workflows, publishes them, restarts n8n and waits until the protected webhook answers. No UI clicks.
Existing databases are upgraded with `python -m tools.migrate`. `main.py` runs it at startup only when a migration login is configured (`MIGRATE_DATABASE_URL` or `ERP_ADMIN_URL`); otherwise it logs a warning and the integrity gate reports anything missing. The same command also syncs the approval windows and ladder from `schemas/policy_constants.py`, so changing a number there is enough. `python scripts/setup_n8n.py` installs or refreshes all four n8n workflows and is safe to re-run. Only an active workflow answers on
`/webhook/supply-chain-rfq`; `/webhook-test/...` works only while "Listen for test event" is on.

If host ports 5432 or 5678 are taken, remap them in a local `docker-compose.override.yml`
(`ports: !override`) and update `DATABASE_URL` and `N8N_WEBHOOK_URL` in `.env` to match.

After editing `db/init.sql`, reset the database with `docker compose down -v && docker compose up -d`.

## Running
| Command | Purpose |
|---|---|
| `python main.py --dry-run` | No-LLM wiring check: ERP watchlist, RAG index, payload schema |
| `python main.py` | One monitoring cycle |
| `python main.py --focus "typhoon Kaohsiung Keelung port closure"` | Bias the Scout toward a known event |
| `python main.py --simulate-search-failure` | Force the first search to fail, to show `[RECOVERY]` |
| `python main.py --watch 30` | Repeat every 30 minutes |
| `python main.py --replay-dlq` | Retry deliveries waiting in the dead-letter queue (also drains legacy `outbox/` files); `--watch` does this every cycle |
| `python main.py --scout-report samples/scout_typhoon.md` | Replay a saved Scout report so the Analyst, Coordinator and n8n run on the demo scenario when the news has no real event |
| `python main.py --no-memory` | Disable CrewAI memory (works without an OpenAI key) |

The Coordinator writes its executive brief to `reports/action_brief.md`.

## How it works
| Agent | Tools | Does |
|---|---|---|
| Disruption Scout | `Web Search` (Serper news, then DuckDuckGo) | At least 3 searches (ports, weather, geopolitics). Reports dated, sourced events with a worst-case delay. |
| Inventory Impact Analyst | `List ERP Tables`, `Describe ERP Tables`, `Query ERP Database` | Writes its own read-only SQL. Applies the lane filter, then computes `gap_days = delay + 5 − cover` and the shortfall. Lists backups and affected POs. |
| Operations Coordinator | `SOP Search` (ChromaDB RAG), `Trigger n8n Procurement Workflow` | Retrieves clauses with `[document, page]`. Applies severity, trigger, quantity (×1.2 rounded up to MOQ), eligibility and approval rules. Sends a validated JSON payload. |

The payload schema is `schemas/payload.py` (Pydantic v2). It requires at least one policy citation, checks
`quantity × price ≈ value` and ties `RFQ` and `MONITOR_ONLY` to the `rfqs` list. n8n turns the payload into
one RFQ email per line and a contingency plan.

**Demo scenario:** a typhoon closes Kaohsiung and Keelung, with a worst-case delay of 21 days. That gives
severity L2 and four RFQs of 22,000 / 15,000 / 12,000 / 26,000 units to S201 / S201 / S202 / S301. The total is
₹5,02,99,000, so the Chief Financial Officer approves. P-3002 exceeds S301's capacity and runs out in 8 days.
P-3001 stays on watch. P-5001 and P-4001 are out of scope for a Taiwan lane.

## Approvals, timeouts and escalation (SOP-SC-015)
RFQ documents are generated at intake but **held** until an approver at or above the SOP-SC-014 section 5 level decides. Each level has 8 hours (L2) or 4 hours (L3); a reminder goes out at 50%; a silent level escalates one step (Operations Manager, Head of SCM, CFO) with a fresh window; after the CFO level the record is **HELD** (nothing is ever released automatically). The clock is `approval_tick()` in the database, run every 5 minutes by n8n.
```bash
python -m tools.approvals status <event_id>
python -m tools.approvals approve <event_id> --as "Chief Financial Officer" --note "ok"   # or: reject
python -m tools.approvals tick          # run the escalation clock once
python -m tools.approvals inbox         # reminders, escalations, holds and Production Planning notices
```
The same decision is available over HTTP: `POST /webhook/supply-chain-approval` with header `X-SCM-Token` and body `{"event_id", "approver_level", "decision": "APPROVE|REJECT", "note"}`.
Clauses 4.5 and 4.7 are executed from the ERP: `stockout_gaps` (backup lead time longer than days of cover, air freight combined) and Production Planning notices are derived, never typed by the model.

## Email notifications (Gmail)
The tick workflow emails reminders, escalations, holds, decisions and Production Planning notices through the n8n credential **SCM Gmail** (authorised once in the n8n UI). Who receives what is configuration, not code, and nothing is emailed until a route exists:
```bash
python -m tools.approvals set-route "Chief Financial Officer" cfo@yourcompany.com
python -m tools.approvals set-route "Production Planning" planning@yourcompany.com
python -m tools.approvals set-route "SCM Alerts" ops@yourcompany.com     # n8n workflow failures
python -m tools.approvals routes
```
Recipients: the three approvers, `Production Planning`, `Procurement`, `SCM Alerts`. A failed send stays queued and is retried (up to 5 attempts, then logged only); unrouted messages are only logged. Google's OAuth redirect URL for the credential is `<n8n URL>/rest/oauth2-credential/callback` (set `N8N_EDITOR_BASE_URL` and `WEBHOOK_URL` on the n8n service when you change its port).

## Data-quality and policy gates
| Gate | Blocks |
|---|---|
| `tools/preflight.py` (runs at the start of every cycle) | the whole crew if the ERP fails a structural check (one PRIMARY per part, valid PO pairs and values, backup emails, ...) or the watchlist / KB index is empty. Stale data only warns unless `ERP_STRICT_FRESHNESS=true`. |
| `schemas/policy_check.py` (inside `trigger_n8n`) | any payload whose approver, severity, supplier eligibility, MOQ multiple, price, quantity formula or citations break SOP-SC-014 / the ERP. The recipient email always comes from the ERP. |
| n8n `Validate & Build` + IF node | HTTP 422 with the list of errors; n8n recomputes totals and the approver itself. |
| `action_log` + `dead_letter` tables | every action is recorded before sending (SOP 6.3) with a state machine; failed deliveries retry with backoff and end in `DEAD` after 6 attempts. |
| CrewAI guardrails (`schemas/guardrails.py`) | the Scout output unless every URL came from a real search and each incident has two independent sources; the Analyst output unless every number matches the ERP. The agent gets precise feedback and retries. |
| `tools/kb_lint.py` | the run if the knowledge base has duplicate or dangling clauses, unknown ERP ids, or numbers that differ from `schemas/policy_constants.py`. |

## Error handling and fallbacks
| Failure | Automatic response | Log tag |
|---|---|---|
| Serper timeout or quota | 3 retries (2/4/8 s), then DuckDuckGo, then `SEARCH UNAVAILABLE` (never invent news) | `[RECOVERY]` |
| Bad SQL | `SQL ERROR` returned to the agent, which rewrites the query | `[RECOVERY]` |
| Unsafe SQL (DROP, UPDATE, …) | `REJECTED`, read-only enforced | |
| Invalid payload | `VALIDATION ERROR` details returned, agent fixes and re-calls | `[RECOVERY]` |
| n8n unreachable | 3 retries, then payload saved to `outbox/` for replay | `[RECOVERY]` |
| Coordinator never triggers n8n | Warning printed, brief still saved | `[WARN]` |

## Testing
```bash
python -m pytest -q      # about 170 tests: ERP data, RAG pages, SQL guard, search fallbacks, n8n tool
```
The ERP tests need the database running. Search, RAG and n8n tests are mocked and spend no credits.

## Module docs
`docs/erp_infra.md`, `docs/knowledge_base.md`, `docs/rag.md`, `docs/scout.md`, `docs/analyst.md`,
`docs/coordinator.md`, `docs/demo_script.md`.

## Team and owners
| Person | Module |
|---|---|
| 1 | `docker-compose.yml`, `db/init.sql`, `n8n/`, `tests/test_erp.py` |
| 2 | `knowledge_base/` PDFs and sources, demo script |
| 3 | `tools/rag_tool.py` |
| 4 | `tools/search_tool.py`, `agents/scout.py` |
| 5 | `tools/sql_tools.py`, `agents/analyst.py` |
| 6 (lead) | `schemas/payload.py`, `tools/n8n_tool.py`, `agents/coordinator.py`, `main.py`, README |

## Video
Loom walkthrough (5 min): _link to be added after recording_
