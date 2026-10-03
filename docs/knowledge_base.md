# Knowledge Base — Page Map (`scm-knowledge-base`)

Owner: the SCM lead (Person 6 maintains it day to day). Originally written by Person 2. Source of truth for every policy citation the agents quote.
Three PDFs generated reproducibly from `knowledge_base/source/build_pdfs.py` (SOP-SC-014 and the contracts are 2 pages each, SOP-SC-015 is 1 page); the same run writes `clauses.jsonl`, the structured index source.

- `knowledge_base/SOP-SC-014_Supply_Disruption_Response.pdf` — disruption rulebook (SOP-SC-014 v3.2)
- `knowledge_base/Vendor_Contracts_and_AVL.pdf` — contracts digest + Approved Vendor List FY 2026-27
- Editable originals: `knowledge_base/source/SOP-SC-014_Supply_Disruption_Response.docx`,
  `knowledge_base/source/Vendor_Contracts_and_AVL.docx`
- Builders: `knowledge_base/source/build_pdfs.py` (canonical), `knowledge_base/source/build_docx.py` (docx export)

Verified 30 Sep 2026: each PDF reports `pages: 2` via pypdf, text is extractable,
clause numbers (`3.2`, `VC-4.1`) appear at line starts. Data-quality gate (7 checks) passes.

## Page map (verified against the built PDFs — Person 3 / Person 6 test against this)

| Question the AI will ask | Document | Page | Clause |
|---|---|---|---|
| How is severity classified? | SOP | 1 | Section 2 |
| When is an RFQ mandatory? | SOP | 1 | 3.2 |
| How is RFQ quantity calculated? | SOP | 1 | 3.3 |
| Which suppliers can receive RFQs? | SOP | 1 | 3.4 |
| When do we need two RFQs? | SOP | 1 | 3.5 |
| When is air freight allowed? | SOP | 2 | 4.1 |
| Safety stock uplift | SOP | 2 | 4.2 |
| Re-routing via another port | SOP | 2 | 4.3 |
| What happens to open POs | SOP | 2 | 4.4 |
| When to warn Production Planning | SOP | 2 | 4.5 |
| Who approves which amount | SOP | 2 | Section 5 |
| Force majeure | Contracts | 1 | VC-1 |
| Penang backup terms | Contracts | 1 | VC-4.1 |
| Bengaluru backup terms | Contracts | 1 | VC-4.3 |
| Approved Vendor List | Contracts | 2 | Schedule A |

Detail — SOP page 1: Sec 1 (1.1–1.2), Sec 2 severity table (L1/L2/L3) + 2.2 conservative-principle (upper end), Sec 3 RFQ rules 3.1–3.6.
SOP page 2: Sec 4 contingencies 4.1–4.6, Sec 5 approval matrix (5.1 + table), Sec 6 records 6.1–6.3.
Contracts page 1: VC-1 force majeure, VC-2 penalty, VC-3 primaries (S101/S102/S103), VC-4 backups (S201/S202/S301/S402).
Contracts page 2: Schedule A AVL (9 vendors, S402 CONDITIONAL), Schedule B RFQ terms B.1–B.4.

All IDs match Part A A4 exactly: S101, S102, S103, S201, S202, S301, S302, S401, S402;
P-1001, P-1002, P-2001, P-3001, P-3002, P-4001, P-5001. Kaohsiung / Keelung / Penang / Cat Lai /
Nhava Sheva / Manesar-WH1 spellings as in A4. No rule outside A5 was added.

## How to edit

1. Edit `knowledge_base/source/build_pdfs.py` (preferred — byte-identical, exact page breaks),
   or edit the `.docx` in `knowledge_base/source/` (font Calibri 10–11) and mirror the change in `build_pdfs.py`.
2. Rebuild: `python knowledge_base/source/build_pdfs.py` (+ `python knowledge_base/source/build_docx.py` if the docx changed).
3. Re-run the data-quality gate from the task brief (SOP 3.2 / 3.3 / 3.4 / Sec 5 CFO / VC-4.1 / S402 / IDs) — all must print PASS.
4. Re-check page breaks still match the table above (Person 3's RAG tests assert `[document, page]`).
5. Never add a rule that isn't in A5 — the RAG tests and Coordinator prompts are pinned to these pages.
6. Formatting rules: clause number at start of its line (`3.2 An RFQ…`, `VC-4.1 Penang…`), IDs exact,
   one `PageBreak` where marked, real text only (no scanned images), no header/footer text.

## Research note 1 — Tables vs sentences for RAG (Part B+)

 finding: kept both tables as tables. pypdf extract on the built PDFs preserves row order, e.g.
`ID | Supplier | Country | Status | Parts covered | S101 | Formosa Microchip Co. | Taiwan | APPROVED | …`
and `Total estimated RFQ value (INR) | Approving authority | Up to 25,00,000 | Operations Manager - Procurement | …`.
Each table is preceded by a semantic anchor sentence (5.1 approval-basis, `Schedule A - Approved Vendor List`,
`Clause VC-4` backup terms) that restates the key in prose, so a chunker retrieving either the sentence
or the row gets the clause number + the value together. Rewriting the AVL as 9 sentences would duplicate
content, risk drift from A5, and split `[document, page]` citations across chunks — so no rewrite was made.
Person 3 chunking recommendation: chunk by clause (one chunk per `3.x` / `VC-x.x` / table + its anchor line),
keep table header in every chunk (`repeatRows`), overlap 1 clause.

## Research note 2 — Terminology consistency (find-replace log)

Standard: A5 wording only — `backup supplier`, `days of cover`, `shortfall units`, `RFQ quantity`,
`APPROVED` / `CONDITIONAL`, `APPROVED_BACKUP`, `HOLD-REVIEW`, `MOQ`, severity `L1/L2/L3`.

| Term avoided | Replaced with | Where checked |
|---|---|---|
| alternate / secondary (for supplier role) | backup | SOP 3.2–3.5, VC-4 headings; kept `alternate` only where it means something else: VC-1.2 `alternate suppliers` during force majeure (legal phrase from MSA) and 4.3 `alternate export port` (port, not supplier) |
| stock / inventory (for cover math) | days of cover / on-hand quantity / daily consumption | 3.1–3.3, 6.2; kept `safety stock` (4.2, defined term), `stock-out` (4.5, defined event), `stock` inside `stock-out` only |
| cover without unit | days of cover | 3.2, 3.6, 6.2 |
| sign off / signoff | sign-off | 3.4, VC-4.4 |
| indent / enquiry | RFQ | throughout |
| Rs / INR confusion | INR with Indian grouping (25,00,000) | Sec 5 table |

Synonym check run over PDF text: `secondary` 0 hits, `alternate supplier` 0 hits outside VC-1.2/4.3 port context,
`days of cover` present in 3.1/3.2/3.6/6.2, supplier IDs + part IDs exact. No meaning was changed.

## Research note 3 — 30-second rule explainer (feeds `docs/demo_script.md` 0:30–1:00)

Show SOP page 1 clause 3.2 highlighted + `v_part_risk` row side by side. Script covers
severity → trigger → quantity in ~45 s (worst-case 21 days → L2; cover < 21+5 so RFQ; shortfall × 1.2 → MOQ).
Full narration in `docs/demo_script.md`.


## Re-audit changes (2026-10-03)
- SOP now carries `Effective: 2026-10-01 · Supersedes: none` and three clarifications that add no numeric rule:
  **2.3** (an event is confirmed only by two independent sources; unconfirmed means L1), **3.6** (cover *equal to or above* delay + 5 is part watch status, closing the gap with 3.2, and no longer reuses the label "L1"),
  **4.1** (freight premium comes from the buyer's carrier quote; never estimated). Page breaks are unchanged (2 pages each).
- `build_docx.py` now renders from `build_pdfs.py`'s flows, so PDF and DOCX cannot drift (`tests/test_kb_consistency.py` checks it, plus Schedule A / lead times against the ERP).
- RAG chunks are clause-atomic (no character overlap, every chunk starts at a clause or section label, each `Section`/`Schedule` heading starts a new chunk). Retrieval keeps only hits within 0.08 of the best, boosts exact clause ids, drops repeated whole-page chunks, and returns `RAG ERROR` for off-topic queries.
- Clause **4.7** added (lead time longer than days of cover: combine the RFQ with 4.1 and state the stock-out gap). It closes the gap where P-1001/S201, P-2001/S202 and P-3002/S301 cannot arrive before stock-out. It adds no numeric threshold.

- Clause **4.5** now also requires notifying Production Planning when the stock-out falls before the earliest backup can deliver (clause 4.7). This reconciles the SOP with the A5 demo expectation (P-3002 runs out in 8 days, backup lead time 14 days) without changing the 7-day number.


## Round 4 changes (2026-10-03): approval timeouts and Clauses 4.5 / 4.7 as executable rules
- **Clauses 4.5 and 4.7 are accepted as written.** They are executed from the ERP by `schemas/policy_check.derive`: 4.7 gives `stockout_gaps` (a backup whose standard lead time exceeds days of cover; air freight is combined with the RFQ, the gap in days is stated), 4.5 gives `production_notifications` (cover within 7 days, or the earliest backup cannot deliver before stock-out). The LLM never supplies these fields; the database turns them into Production Planning notifications.
- **New document `SOP-SC-015_Approval_Timeouts_and_Escalation.pdf` (1 page).** Approval window 8 h (L2) / 4 h (L3), the same as the response windows in SOP-SC-014 section 2; a reminder at 50% of the window; escalation one level up (Operations Manager, then Head of SCM, then CFO) with a fresh window; HELD after the last level, with no automatic approval and no bypass at any amount. Its numbers are generated from `schemas/policy_constants.py`, so the PDF, the database seed and the n8n logic cannot disagree (`tools/kb_lint.py` and the tests enforce it).
- **Nothing in the knowledge base needed purging.** No PDF or clause named a person as a gatekeeper; "Person 2" appeared only as an author label in these docs.

## Change procedure (no gatekeeper)
Any team member may change policy text; the checks below replace a reviewer:
1. Edit the clause in `knowledge_base/source/build_pdfs.py` (numbers come from `schemas/policy_constants.py`; change a number there, never in the text).
2. Rebuild: `cd knowledge_base/source && python build_pdfs.py && python build_docx.py` (PDFs, DOCX and `clauses.jsonl`).
3. Run `python -m tools.kb_lint` (unique clause ids, resolvable references, ERP ids, numbers equal to the constants), then `python -m pytest -q` (page counts, DOCX equals PDF, retrieval and citation tests).
4. If a policy number changed (approval windows, reminder share, ladder), run `python -m tools.migrate`: it syncs the stored approval policy from `schemas/policy_constants.py`. Never edit a migration that was already applied (its checksum is verified); open approvals keep their deadline, new ones use the new window. Until the sync and the PDF rebuild are done, `tests/test_approvals_db.py`, `kb_lint` and the startup gate fail.
5. Re-index happens automatically (the index name hashes the PDFs and `clauses.jsonl`).
