"""Deterministic SOP-SC-014 / ERP checks applied to the LLM's payload before anything is sent.
Pure functions: the ERP snapshot is passed in, so they are unit-testable without a database."""
import math

from schemas.policy_constants import (BUFFER_DAYS, QTY_UPLIFT, STOCKOUT_NOTIFY_DAYS, approver_for, severity_for)

_SEV_RANK = {"L1": 1, "L2": 2, "L3": 3}


def load_erp(engine) -> dict:
    """Snapshot of what the checks need, read through a pooled SQLAlchemy engine and validated row by row
    (a NULL, negative or malformed value raises instead of silently producing a wrong decision)."""
    from sqlalchemy import text
    from schemas.ingress import validate_erp_rows
    with engine.connect() as c:
        sp = [dict(r) for r in c.execute(text(
            "SELECT sp.supplier_id, sp.part_id, sp.sourcing_role, sp.unit_price_inr, sp.moq, sp.monthly_capacity, "
            "s.avl_status, s.contact_email, s.standard_lead_days FROM supplier_parts sp JOIN suppliers s USING (supplier_id)")).mappings()]
        inv = [dict(r) for r in c.execute(text("SELECT part_id, on_hand_qty, daily_consumption FROM inventory")).mappings()]
    return validate_erp_rows(sp, inv)


def check(p: dict, erp: dict, retrieved: set | None = None, retrieved_clauses: set | None = None) -> list[str]:
    """Return a list of fixable error strings (empty = payload is policy-compliant).
    Also overwrites each RFQ's supplier_email with the ERP value: the recipient is never LLM-controlled."""
    errs = []
    rfqs, parts = p.get("rfqs", []), p.get("affected_parts", [])
    total = sum(r["est_value_inr"] for r in rfqs)
    if rfqs and p["approval_authority"] != approver_for(total):
        errs.append(f"approval_authority must be '{approver_for(total)}' for total RFQ value {total:,.0f} (SOP 5)")
    worst = max((a["projected_delay_days"] for a in parts), default=0)
    if _SEV_RANK[p["severity"]] < _SEV_RANK[severity_for(worst)]:
        errs.append(f"severity must be at least {severity_for(worst)} for worst-case delay {worst} days (SOP 2)")
    for r in rfqs:
        sid, pid = r["supplier_id"], r["part_id"]
        row = erp.get((sid, pid))
        if not row or row["role"] != "APPROVED_BACKUP":
            errs.append(f"{sid}/{pid} is not an APPROVED_BACKUP pair in the ERP (SOP 3.4); pick a supplier from the Analyst's backup table")
            continue
        if row["avl"] != "APPROVED":
            errs.append(f"{sid} is {row['avl']}: needs Head of SCM sign-off before an RFQ, so do not RFQ it (SOP 3.4)")
        if r["quantity"] % row["moq"]:
            errs.append(f"{pid} quantity {r['quantity']} is not a multiple of {sid}'s MOQ {row['moq']} (SOP 3.3)")
        if abs(r["unit_price_inr"] - row["price"]) > 0.01:
            errs.append(f"{pid} unit_price_inr must be the ERP price {row['price']} for {sid}")
        part = erp.get("parts", {}).get(pid)
        if part:
            short = max(0.0, worst + BUFFER_DAYS - part["cover"]) * part["daily"]
            need = math.ceil(short * QTY_UPLIFT / row["moq"]) * row["moq"]
            low_ok = _SEV_RANK[p["severity"]] == 3          # L3 may legitimately over-order
            if (r["quantity"] < need) or (r["quantity"] != need and not low_ok):
                errs.append(f"{pid} quantity must be ceil(shortfall x 1.2 / MOQ) x MOQ = {need} for delay {worst} (SOP 3.3)")
        r["supplier_email"] = row["email"]
    if retrieved is not None:
        for c in p.get("policy_citations", []):
            if (c["document"], c["page"]) not in retrieved:
                errs.append(f"citation {c['document']} page {c['page']} was never returned by 'SOP Search'; "
                            "run SOP Search for that clause and cite only what it returned")
    if retrieved_clauses:
        for c in p.get("policy_citations", []):
            if (c["document"], c["page"]) in (retrieved or set()) and \
               (c["document"], c["page"], str(c["clause"]).lower().strip()) not in retrieved_clauses:
                errs.append(f"citation clause '{c['clause']}' on {c['document']} page {c['page']} was not among the clauses "
                            "'SOP Search' returned; cite a clause id that appeared in the search results")
    return errs


def derive(p: dict, erp: dict) -> dict:
    """Execute SOP-SC-014 clauses 4.5 and 4.7 from the ERP, never from the LLM. Writes `stockout_gaps` and
    `production_notifications` into the payload (replacing anything supplied) and appends matching contingency lines.
      4.7: a backup whose standard lead time is longer than the part's days of cover -> RFQ combined with air freight (4.1)
           and the stock-out gap (lead time minus cover) stated;
      4.5: Production Planning is notified when cover <= 7 days, or when the earliest backup cannot deliver before stock-out."""
    parts = erp.get("parts", {})
    gaps, earliest = [], {}
    for r in p.get("rfqs", []):
        row, part = erp.get((r["supplier_id"], r["part_id"])), parts.get(r["part_id"])
        if not row or not part or "lead_days" not in row:
            continue
        earliest[r["part_id"]] = min(earliest.get(r["part_id"], (10**9, "")), (row["lead_days"], r["supplier_id"]))
        if row["lead_days"] > part["cover"]:
            gaps.append({"part_id": r["part_id"], "supplier_id": r["supplier_id"], "days_of_cover": round(part["cover"], 1),
                         "backup_lead_days": row["lead_days"], "gap_days": round(row["lead_days"] - part["cover"], 1),
                         "expedite_air_freight": True})
    notes, seen = [], set()
    for a in p.get("affected_parts", []):
        pid = a["part_id"]
        part = parts.get(pid)
        if not part or pid in seen:
            continue
        reason = None
        if part["cover"] <= STOCKOUT_NOTIFY_DAYS:
            reason = f"stock-out in {part['cover']:.0f} days (SOP-SC-014 4.5, within {STOCKOUT_NOTIFY_DAYS} days)"
        elif pid in earliest and earliest[pid][0] > part["cover"]:
            lead, sid = earliest[pid]
            reason = (f"stock-out in {part['cover']:.0f} days but the earliest backup ({sid}) delivers in {lead} days, "
                      f"{lead - part['cover']:.0f} days late (SOP-SC-014 4.5, 4.7)")
        if reason:
            seen.add(pid)
            notes.append({"part_id": pid, "days_of_cover": round(part["cover"], 1), "reason": reason})
    p["stockout_gaps"], p["production_notifications"] = gaps, notes
    extra = [f"Combine the RFQ for {g['part_id']} with air freight (SOP-SC-014 4.1, 4.7): stock-out gap {g['gap_days']:g} days" for g in gaps]
    extra += [f"Notify Production Planning about {n['part_id']}: {n['reason']}" for n in notes]
    p["contingency_actions"] = list(p.get("contingency_actions", [])) + [e for e in extra if e not in p.get("contingency_actions", [])]
    return p
