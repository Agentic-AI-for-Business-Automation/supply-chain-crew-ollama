"""Lint the knowledge base: unique clause ids, resolvable cross-references, IDs that exist in the ERP,
and every numeric fact equal to schemas/policy_constants.py. Runs in the tests and in the preflight gate."""
import json, os, re

from schemas import policy_constants as pc

KB = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "knowledge_base"))


def inr(n: int) -> str:
    """Indian digit grouping: 2500000 -> 25,00,000."""
    s = str(n)
    head, tail = s[:-3], s[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"{head},{tail}" if head else tail


def load_records(kb_dir: str = KB) -> list[dict]:
    with open(os.path.join(kb_dir, "clauses.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


FACTS = {   # document prefix -> list of (human label, regex that must appear in that document's clause text)
    "SOP-SC-014": [
        ("severity L1 bound", rf"under {pc.L1_BELOW_DAYS} days"),
        ("severity L2 range", rf"{pc.L1_BELOW_DAYS} to {pc.L2_MAX_DAYS} days"),
        ("severity L3 bound", rf"over {pc.L2_MAX_DAYS} days"),
        ("trigger buffer", rf"\+ {pc.BUFFER_DAYS}-day safety buffer"),
        ("shortfall buffer", rf"projected delay \+ {pc.BUFFER_DAYS} - days of cover"),
        ("quantity uplift", rf"shortfall units x {pc.QTY_UPLIFT}\b"),
        ("air freight premium", rf"below {pc.AIR_FREIGHT_MAX_PREMIUM_PCT}% of the affected PO value"),
        ("safety stock uplift", rf"raised by {pc.SAFETY_STOCK_UPLIFT_PCT}%"),
        ("stock-out notify window", rf"within {pc.STOCKOUT_NOTIFY_DAYS} days"),
        ("approval ops limit", rf"Up to {re.escape(inr(pc.OPS_LIMIT_INR))}\b"),
        ("approval head range", rf"{re.escape(inr(pc.OPS_LIMIT_INR + 1))} to {re.escape(inr(pc.HEAD_LIMIT_INR))}"),
        ("approval CFO limit", rf"Above {re.escape(inr(pc.HEAD_LIMIT_INR))}"),
        ("confirmation sources", r"two independent sources"),
        ("retention", rf"retained for {pc.RECORD_RETENTION_YEARS} years"),
    ],
    "SOP-SC-015": [
        ("approval windows", rf"{pc.APPROVAL_WINDOW_HOURS['L2']} hours for an L2 action and {pc.APPROVAL_WINDOW_HOURS['L3']} hours for an L3 action"),
        ("reminder share", rf"When {pc.APPROVAL_REMINDER_PCT}% of the window has passed"),
        ("escalation ladder", ", then ".join(re.escape(n) for n in pc.APPROVAL_LADDER)),
        ("hold has no bypass", r"no automatic approval and no bypass"),
        ("record retention", rf"kept for {pc.RECORD_RETENTION_YEARS} years"),
        ("production notice window", rf"within {pc.STOCKOUT_NOTIFY_DAYS} days"),
    ],
}


def lint(records: list[dict], erp_supplier_ids: set[str] | None = None, erp_part_ids: set[str] | None = None) -> list[str]:
    errs: list[str] = []
    seen: set[tuple[str, str]] = set()
    ids_by_doc: dict[str, set[str]] = {}
    for r in records:
        key = (r["doc"], r["clause"])
        if key in seen:
            errs.append(f"duplicate clause id {r['clause']} in {r['doc']}")
        seen.add(key)
        ids_by_doc.setdefault(r["doc"], set()).add(r["clause"])
        if r["kind"] == "clause" and r["clause"] not in r["text"]:
            errs.append(f"clause {r['clause']} text does not contain its own number")
    all_ids = set().union(*ids_by_doc.values()) if ids_by_doc else set()
    sections = {r["section"] for r in records}
    for r in records:
        for ref in re.findall(r"(?:clauses?|measures?)\s+((?:\d+\.\d+|VC-\d+\.\d+)(?:\s+(?:or|and)\s+(?:\d+\.\d+|VC-\d+\.\d+))*)", r["text"]):
            for one in re.findall(r"\d+\.\d+|VC-\d+\.\d+", ref):
                if one not in all_ids:
                    errs.append(f"{r['clause']} refers to clause {one}, which does not exist")
        for ref in re.findall(r"Schedule ([AB])\b", r["text"]):
            if f"Schedule {ref}" not in sections:
                errs.append(f"{r['clause']} refers to Schedule {ref}, which does not exist")
        if erp_supplier_ids is not None:
            for sid in re.findall(r"\bS\d{3}\b", r["text"]):
                if sid not in erp_supplier_ids:
                    errs.append(f"{r['clause']} mentions supplier {sid}, which is not in the ERP")
        if erp_part_ids is not None:
            for pid in re.findall(r"\bP-\d{4}\b", r["text"]):
                if pid not in erp_part_ids:
                    errs.append(f"{r['clause']} mentions part {pid}, which is not in the ERP")
    for prefix, facts in FACTS.items():
        doc_text = " ".join(re.sub(r"\s+", " ", r["text"]) for r in records if r["doc"].startswith(prefix))
        if not doc_text:
            errs.append(f"document {prefix} is missing from the knowledge base")
            continue
        for label, pattern in facts:
            if not re.search(pattern, doc_text):
                errs.append(f"{prefix} fact '{label}' does not match policy_constants (expected pattern: {pattern})")
    return errs


if __name__ == "__main__":
    problems = lint(load_records())
    print("\n".join(problems) or "knowledge base lint: OK")
    raise SystemExit(1 if problems else 0)
