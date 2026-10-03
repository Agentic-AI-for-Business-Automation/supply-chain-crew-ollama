"""SOP-SC-014 rules applied to payloads: one good case from the A5 demo, one mutation per rule."""
import copy
import pytest
from schemas.policy_check import approver_for, check, severity_for

SOP = "SOP-SC-014_Supply_Disruption_Response.pdf"
ERP = {("S201", "P-1001"): dict(role="APPROVED_BACKUP", price=452.0, moq=2000, capacity=30000, avl="APPROVED", email="rfq@penang.example"),
       ("S401", "P-5001"): dict(role="PRIMARY", price=265.0, moq=200000, capacity=3500000, avl="APPROVED", email="x@sz.example"),
       ("S402", "P-5001"): dict(role="APPROVED_BACKUP", price=298.0, moq=100000, capacity=900000, avl="CONDITIONAL", email="y@pune.example"),
       "parts": {"P-1001": dict(cover=12.0, daily=1200), "P-5001": dict(cover=25.0, daily=96000)}}
GOOD = dict(severity="L2", approval_authority="Head of Supply Chain Management", sources=["https://a.example/x"],
            affected_parts=[dict(part_id="P-1001", projected_delay_days=21)],
            rfqs=[dict(supplier_id="S201", part_id="P-1001", quantity=22000, unit_price_inr=452.0,
                       est_value_inr=9_944_000, supplier_email="")],
            policy_citations=[dict(document=SOP, page=1, clause="3.2")])


def run(**over):
    p = copy.deepcopy(GOOD); p.update(over)
    return check(p, ERP, {(SOP, 1)})


def test_demo_case_is_clean_and_email_is_overwritten():
    p = copy.deepcopy(GOOD)
    assert check(p, ERP, {(SOP, 1)}) == []
    assert p["rfqs"][0]["supplier_email"] == "rfq@penang.example"


@pytest.mark.parametrize("total,who", [(2_500_000, "Operations Manager - Procurement"), (2_500_001, "Head of Supply Chain Management"),
                                       (10_000_000, "Head of Supply Chain Management"), (50_299_000, "Chief Financial Officer")])
def test_approver_boundaries(total, who):
    assert approver_for(total) == who


@pytest.mark.parametrize("d,sev", [(6, "L1"), (7, "L2"), (21, "L2"), (22, "L3")])
def test_severity_boundaries(d, sev):
    assert severity_for(d) == sev


def test_wrong_approver():
    assert any("approval_authority" in e for e in run(approval_authority="Operations Manager - Procurement"))


def test_severity_too_low_but_higher_is_allowed():
    assert any("severity" in e for e in run(severity="L1"))
    assert not any("severity" in e for e in run(severity="L3"))


def test_unknown_supplier_pair():
    assert any("not an APPROVED_BACKUP" in e for e in run(rfqs=[dict(GOOD["rfqs"][0], supplier_id="S999")]))


def test_primary_supplier_is_not_eligible():
    assert any("APPROVED_BACKUP" in e for e in run(rfqs=[dict(GOOD["rfqs"][0], supplier_id="S401", part_id="P-5001")]))


def test_conditional_supplier_needs_signoff():
    r = dict(GOOD["rfqs"][0], supplier_id="S402", part_id="P-5001", quantity=100000, unit_price_inr=298.0, est_value_inr=29_800_000)
    assert any("CONDITIONAL" in e for e in run(rfqs=[r], affected_parts=[dict(part_id="P-5001", projected_delay_days=21)]))


def test_quantity_not_moq_multiple_or_wrong_formula():
    errs = run(rfqs=[dict(GOOD["rfqs"][0], quantity=7, est_value_inr=7 * 452)])
    assert any("MOQ" in e for e in errs) and any("ceil(shortfall" in e for e in errs)


def test_wrong_price():
    assert any("ERP price" in e for e in run(rfqs=[dict(GOOD["rfqs"][0], unit_price_inr=1.0, est_value_inr=22000)]))


def test_uncited_page():
    assert any("never returned" in e for e in run(policy_citations=[dict(document=SOP, page=2, clause="5")]))
