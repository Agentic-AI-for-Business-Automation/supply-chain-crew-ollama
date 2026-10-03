"""SOP-SC-014 clauses 4.5 and 4.7 executed from the ERP: A5 demo numbers are the oracle."""
import copy
from schemas.policy_check import derive

ERP = {("S201", "P-1001"): dict(lead_days=21), ("S201", "P-1002"): dict(lead_days=21), ("S202", "P-2001"): dict(lead_days=24),
       ("S301", "P-3002"): dict(lead_days=14), ("S301", "P-1001"): dict(lead_days=14),
       "parts": {"P-1001": dict(cover=12.0), "P-1002": dict(cover=21.0), "P-2001": dict(cover=18.0), "P-3002": dict(cover=8.0),
                 "P-3001": dict(cover=35.0), "P-9001": dict(cover=5.0)}}


def payload(rfqs, parts=("P-1001", "P-1002", "P-2001", "P-3002")):
    return {"rfqs": [dict(supplier_id=s, part_id=p) for s, p in rfqs], "affected_parts": [dict(part_id=p) for p in parts],
            "contingency_actions": ["Raise safety stock by 30% (SOP 4.2)"]}


A5 = [("S201", "P-1001"), ("S201", "P-1002"), ("S202", "P-2001"), ("S301", "P-3002")]


def test_a5_gaps_and_notifications():
    p = derive(payload(A5), ERP)
    gaps = {g["part_id"]: g for g in p["stockout_gaps"]}
    assert set(gaps) == {"P-1001", "P-2001", "P-3002"}                          # P-1002: lead 21 is not longer than cover 21
    assert [gaps[k]["gap_days"] for k in ("P-1001", "P-2001", "P-3002")] == [9, 6, 6] and all(g["expedite_air_freight"] for g in gaps.values())
    assert {n["part_id"] for n in p["production_notifications"]} == {"P-1001", "P-2001", "P-3002"}
    assert "S301" in next(n["reason"] for n in p["production_notifications"] if n["part_id"] == "P-3002")
    assert any("air freight" in c and "P-3002" in c for c in p["contingency_actions"])
    assert any(c.startswith("Notify Production Planning about P-3002") for c in p["contingency_actions"])
    assert "Raise safety stock by 30% (SOP 4.2)" in p["contingency_actions"]    # existing lines are kept


def test_equal_lead_time_and_cover_is_not_a_gap():
    p = derive(payload([("S201", "P-1002")], parts=("P-1002",)), ERP)
    assert p["stockout_gaps"] == [] and p["production_notifications"] == []


def test_cover_within_seven_days_notifies_even_without_an_rfq():
    p = derive(payload([], parts=("P-9001", "P-3001")), ERP)
    assert [n["part_id"] for n in p["production_notifications"]] == ["P-9001"] and p["stockout_gaps"] == []


def test_eight_days_of_cover_does_not_notify_by_itself_but_a_late_backup_does():
    erp = copy.deepcopy(ERP); erp[("S301", "P-3002")]["lead_days"] = 5          # backup faster than cover
    p = derive(payload([("S301", "P-3002")], parts=("P-3002",)), erp)
    assert p["production_notifications"] == [] and p["stockout_gaps"] == []
    p = derive(payload([("S301", "P-3002")], parts=("P-3002",)), ERP)
    assert len(p["production_notifications"]) == 1


def test_dual_rfq_uses_the_earliest_backup_for_clause_45():
    p = derive(payload([("S201", "P-1001"), ("S301", "P-1001")], parts=("P-1001",)), ERP)
    assert "S301" in p["production_notifications"][0]["reason"] and "14 days" in p["production_notifications"][0]["reason"]
    assert {g["supplier_id"] for g in p["stockout_gaps"]} == {"S201", "S301"}   # 4.7 is stated per RFQ line


def test_llm_supplied_values_are_replaced_and_derivation_is_idempotent():
    p = payload(A5); p["stockout_gaps"] = [{"part_id": "P-1002", "gap_days": 99}]; p["production_notifications"] = [{"part_id": "P-1002"}]
    once = derive(p, ERP); twice = derive(copy.deepcopy(once), ERP)
    assert {g["part_id"] for g in once["stockout_gaps"]} == {"P-1001", "P-2001", "P-3002"}
    assert once == twice


def test_unknown_rows_are_skipped_not_crashed():
    p = derive(payload([("S999", "P-1001")], parts=("P-0000",)), ERP)
    assert p["stockout_gaps"] == [] and p["production_notifications"] == []
