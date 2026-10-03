"""Integrity gate: the crew (and therefore n8n) must not run on ERP data or a knowledge base that fails basic checks."""
import os, re

from schemas import policy_constants as pc

_CRED = re.compile(r"://[^/@\s]+@")


def _scrub(msg) -> str:
    return _CRED.sub("://***@", str(msg))

HARD = [   # structural problems: any returned row blocks the run
    ("each part has exactly one PRIMARY supplier",
     "SELECT p.part_id FROM parts p LEFT JOIN supplier_parts sp ON sp.part_id=p.part_id AND sp.sourcing_role='PRIMARY' "
     "GROUP BY p.part_id HAVING count(sp.part_id) <> 1"),
    ("every part has an inventory row",
     "SELECT part_id FROM parts WHERE part_id NOT IN (SELECT part_id FROM inventory)"),
    ("open PO supplier/part pair exists in supplier_parts",
     "SELECT po_id FROM purchase_orders po WHERE NOT EXISTS (SELECT 1 FROM supplier_parts sp "
     "WHERE sp.supplier_id=po.supplier_id AND sp.part_id=po.part_id)"),
    ("PO value equals quantity x unit price (1%)",
     "SELECT po_id FROM purchase_orders WHERE abs(po_value_inr - quantity*unit_price_inr) > 0.01*po_value_inr"),
    ("every backup supplier has a contact email",
     "SELECT sp.supplier_id FROM supplier_parts sp JOIN suppliers s USING (supplier_id) "
     "WHERE sp.sourcing_role='APPROVED_BACKUP' AND coalesce(s.contact_email,'') = ''"),
    ("no non-positive consumption, MOQ or price",
     "SELECT i.part_id FROM inventory i WHERE i.daily_consumption <= 0 "
     "UNION SELECT part_id FROM supplier_parts WHERE moq <= 0 OR unit_price_inr <= 0"),
]
_LADDER = ", ".join(f"({i}, '{n}')" for i, n in enumerate(pc.APPROVAL_LADDER, 1))
_WINDOWS = ", ".join(f"('{s}', {h * 60}, {pc.APPROVAL_REMINDER_PCT})" for s, h in sorted(pc.APPROVAL_WINDOW_HOURS.items()))
HARD += [   # SOP-SC-015: the ladder and windows stored in the database must equal the constants every other layer uses
    ("approval ladder equals policy_constants",
     f"SELECT rank FROM approval_levels WHERE (rank, name) NOT IN (VALUES {_LADDER}) "
     f"UNION SELECT 0 WHERE (SELECT count(*) FROM approval_levels) <> {len(pc.APPROVAL_LADDER)}"),
    ("approval windows equal policy_constants",
     f"SELECT severity FROM approval_policy WHERE (severity, window_minutes, reminder_pct) NOT IN (VALUES {_WINDOWS}) "
     f"UNION SELECT 'count' WHERE (SELECT count(*) FROM approval_policy) <> {len(pc.APPROVAL_WINDOW_HOURS)}"),
]
SOFT = [   # freshness: warning unless ERP_STRICT_FRESHNESS=true
    ("inventory snapshot older than {age} days",
     "SELECT part_id FROM inventory WHERE CURRENT_DATE - last_updated > {age}"),
    ("open PO already past its expected delivery date",
     "SELECT po_id FROM purchase_orders WHERE status IN ('CONFIRMED','IN_TRANSIT') AND expected_delivery < CURRENT_DATE"),
]


class PreflightError(RuntimeError):
    pass


def _rows(db, sql):
    return [tuple(r.values()) for r in db._execute(sql)]


def erp_integrity_gate(db, max_age_days: int | None = None) -> tuple[list[str], list[str]]:
    """Return (blocking, warnings) as readable strings. Empty `blocking` means the ERP passed."""
    age = max_age_days if max_age_days is not None else int(os.getenv("ERP_MAX_AGE_DAYS", "7"))
    blocking, warnings = [], []
    for name, sql in HARD:
        try:
            bad = _rows(db, sql)
        except Exception as e:
            blocking.append(f"{name}: check could not run ({_scrub(e)[:120]})")
            continue
        if bad:
            blocking.append(f"{name}: {[b[0] for b in bad][:5]}")
    for name, sql in SOFT:
        try:
            bad = _rows(db, sql.format(age=age))
        except Exception:
            continue
        if bad:
            warnings.append(f"{name.format(age=age)}: {[b[0] for b in bad][:5]}")
    return blocking, warnings


def run_gate(db, log) -> None:
    """Log every result; raise PreflightError when the cycle must not start."""
    blocking, warnings = erp_integrity_gate(db)
    for w in warnings:
        log("WARN", f"ERP freshness: {w}")
    strict = os.getenv("ERP_STRICT_FRESHNESS", "false").lower() == "true"
    if blocking or (strict and warnings):
        for b in blocking + (warnings if strict else []):
            log("WARN", f"ERP integrity FAILED: {b}")
        raise PreflightError("ERP failed integrity checks; the crew was not started: " + "; ".join(blocking + (warnings if strict else []))[:400])
    log("BOOT", f"ERP integrity gate passed ({len(HARD)} structural checks, {len(warnings)} freshness warning(s))")
