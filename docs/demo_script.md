# Demo Script — 5-minute Loom video (the SCM lead directs and narrates, Person 6 drives)

Total runtime: ≤ 5:00. Proves three things: agents talking to each other, tools being used, error recovery.
Scenario (fixed for all members): typhoon closes Kaohsiung + Keelung, shipments 14–21 days late → worst case
21 days → severity L2 → RFQs for P-1001 / P-1002 / P-2001 / P-3002, P-3001 on watch, total ₹5,02,99,000 → CFO approval.

- Speaker: one narrator (the SCM lead: explains severity → trigger → quantity → approval). Person 6 drives the keyboard silently.
- Recording tool: Loom (loom.com), free tier. Mode: Screen only (fallback Screen + Camera if the team wants a face). 1080p, full screen, mic on.
- Command run on camera: `python main.py --simulate-search-failure --focus "typhoon Kaohsiung Keelung port closure"`
- If today's news has no real typhoon, use the replay: `python main.py --scout-report samples/scout_typhoon.md` (the Analyst, Coordinator, n8n and approval steps below run for real).
- Approval step (after the run, about 4:10): `python -m tools.approvals status <event_id>` shows `PENDING` at the CFO level with a deadline and the RFQ documents held; then `python -m tools.approvals approve <event_id> --as "Chief Financial Officer"` releases them. To show escalation without waiting, run `python -m tools.approvals tick` after moving the deadline (see `scripts/chaos_check.py`).

## Second-by-second plan (narration lines are exact — read verbatim)

| Time | On screen | Narration | Rubric point proven |
|---|---|---|---|
| 0:00–0:30 | Architecture diagram (Scout → Analyst → Coordinator, tools, Postgres + n8n) + terminal `docker compose ps` (both `erp-postgres` and `n8n` "Up") | "A typhoon has closed Taiwan's Kaohsiung and Keelung ports. Our three-agent crew — Scout, Analyst, Coordinator — turns that news into approved backup orders in one run." | Docker setup, working system |
| 0:30–1:00 | `v_part_risk` table in terminal/DBeaver (P-1001 cover 12, P-3002 cover 8) side-by-side with SOP page 1, clause 3.2 highlighted: "days of cover < projected delay + 5-day safety buffer" | "Here is our stock and our rule. Worst-case delay is 21 days, so this is a severity L2. Any part with cover below 21 plus 5 must raise an RFQ — shortfall times 1.2, rounded to the supplier's MOQ." | Real data and policy, strategic justification |
| 1:00–1:45 | Terminal: `python main.py --simulate-search-failure --focus "typhoon Kaohsiung Keelung port closure"`. Scout calls `web_search`, first call returns `SEARCH UNAVAILABLE`, next line `[RECOVERY]` fallback fires and Scout continues | "Watch the Scout. Its first web search fails — and there is the recovery line. It falls back to the cached advisory and hands confirmed port-closure evidence to the Analyst. No crash, no stall." | Error recovery, tool calling, agent autonomy |
| 1:45–2:45 | Analyst calls `list_erp_tables`, `describe_erp_tables`, then `query_erp` with its own SQL over stock, consumption, suppliers; one SQL error + fix visible (`SQL ERROR:` then corrected query, rows return) | "The Analyst writes its own database queries — lists the tables, inspects the schema, queries cover and consumption, even fixes a bad query by itself — and computes the shortfalls our rulebook demands." | Agent autonomy, tool calling, error recovery |
| 2:45–3:45 | Coordinator calls `sop_search` (RAG over the company SOP and contract PDFs). Hits on screen showing `[SOP-SC-014, p.1, 3.2]`, `[SOP-SC-014, p.2, Sec 5]`, `[Contracts, p.2, Schedule A]`. RFQ table builds: P-1001 22,000 S201; P-1002 15,000 S201; P-2001 12,000 S202; P-3002 26,000 S301 | "Every decision is backed by a rule. Severity L2 from Section 2, RFQs from clause 3.2, quantities from 3.3, suppliers from 3.4 and the Approved Vendor List — each with document and page." | Strategic justification (25%), RAG grounding |
| 3:45–4:30 | Terminal `[N8N] Webhook accepted` + n8n Executions page: `supply-chain-rfq` execution green, RFQ email nodes for S201/S202/S301 | "The order request lands in the buying system. The Coordinator posts one payload to n8n, and the workflow fires the RFQ emails to Penang, Saigon and Bengaluru." | Functional integration (n8n) |
| 4:10–4:30 | Terminal: `python -m tools.approvals status <event_id>` (PENDING at the CFO level, deadline shown, documents held), then `approve ... --as "Chief Financial Officer"`; n8n Executions shows the `SCM Approval Decision` run | "The orders are not sent on the agents' say-so. They wait for the approver the SOP names. If nobody answers, the request escalates one level per missed window and finally holds; nothing is released without a person." | Human control, SOP-SC-015 |
| 4:30–5:00 | `reports/action_brief.md` open: severity L2, 4 RFQs + P-3001 watch, total ₹5,02,99,000 → Chief Financial Officer, P-3002 capacity flag (26,000 needed vs 15,000/month → air-freight / re-route per 4.1+4.3), P-3002 stock-out in 8 days → Production Planning notified (4.5), citations | "Four backup orders, one approver — the CFO — every line traceable to the rulebook. A disruption becomes a costed, approved plan before the line stops. That is the business value." | Decision quality, business value |

## Before-recording checklist (morning of recording)

- [ ] Terminal font 18 pt+, full-screen, notifications / Slack / mail OFF, bookmarks bar hidden.
- [ ] `.env` NEVER shown on screen (`cat .env` forbidden; `OPENAI_API_KEY`/`SERPER_API_KEY` masked). Share screen only after `docker compose ps`.
- [ ] All four n8n workflows ACTIVE (`python scripts/setup_n8n.py` installs them; header token is in `.env`), Executions page logged in and empty/filtered to the workflow.
- [ ] Postgres up with Person 1 seed data (`v_part_risk` returns the 5 demo rows).
- [ ] PDFs in `knowledge_base/` (2 pages each), RAG index builds cleanly (`build_index()` OK).
- [ ] One full dry run done that morning; `reports/action_brief.md` from the dry run saved as backup.
- [ ] Loom: 1080p, mic test 10 s, "record full screen" selected. Water for the speaker.

## If the run is too long / live demo rules

- Record the full run once; trim only waiting parts (model thinking, pip, index build) in Loom's editor.
- Never cut: the `SEARCH UNAVAILABLE` → `[RECOVERY]` moment, the Analyst's SQL error → fix, any `sop_search [document, page]` hit, the `[N8N] Webhook accepted` line.
- Keep Loom chapters/timestamps matching the table above so the examiner can jump to error recovery (1:00) and justification (2:45).
- Backup plan: if the live run fails on recording day, play the saved recording + scroll the saved `reports/action_brief.md`
  (severity L2, ₹5,02,99,000 → CFO, P-3002 capacity + Production-Planning flags) and narrate over it — the brief alone proves decision quality.
