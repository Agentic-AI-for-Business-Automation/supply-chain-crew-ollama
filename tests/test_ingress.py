"""Layer 2: deterministic validation of ERP rows, Scout text and Analyst text (A5 numbers are the oracle)."""
from datetime import date
import pytest
from schemas import ingress, policy_constants as pc
from schemas.guardrails import make_analyst_guardrail, scout_guardrail

TODAY = date(2026, 10, 3)
URLS = {"https://reuters.example/typhoon-kaohsiung", "https://cna.example/taiwan-ports-shut", "https://port.example/keelung"}
SCOUT_OK = """## Disruption events
1. Location/port: Kaohsiung port | Country: Taiwan
   Type: weather (typhoon) - port closure
   Date: 2026-10-01
   Delay range: 14-21 days | Worst-case delay: 21 days
   Confidence: high
   Sources: https://reuters.example/typhoon-kaohsiung, https://cna.example/taiwan-ports-shut
2. Location/port: Keelung port | Country: Taiwan
   Type: weather (typhoon) - port closure
   Date: 2026-10-01
   Delay range: 14-21 days | Worst-case delay: 21 days
   Confidence: high
   Sources: https://port.example/keelung
## Verdict
MATERIAL DISRUPTION FOUND"""
SCOUT_NONE = "## Disruption events\n## Verdict\nNO MATERIAL DISRUPTION FOUND"

ERP = {"parts": {  # (cover, daily, primary, country, port) exactly as seeded
    "P-1001": dict(cover=12.0, daily=1200, primary="S101", country="Taiwan", port="Kaohsiung"),
    "P-1002": dict(cover=21.0, daily=2400, primary="S101", country="Taiwan", port="Kaohsiung"),
    "P-2001": dict(cover=18.0, daily=1200, primary="S102", country="Taiwan", port="Keelung"),
    "P-3001": dict(cover=35.0, daily=1200, primary="S103", country="Taiwan", port="Kaohsiung"),
    "P-3002": dict(cover=8.0, daily=1200, primary="S103", country="Taiwan", port="Kaohsiung"),
    "P-5001": dict(cover=25.0, daily=96000, primary="S401", country="China", port="Yantian")}}


def analyst(rows):
    head = "## Exposed parts\n| part_id | part_name | critical | primary_supplier_id | days_of_cover | daily_consumption | worst_case_delay | gap_days | shortfall_units |\n"
    return head + "\n".join("| " + " | ".join(map(str, r)) + " |" for r in rows) + "\n## Affected open POs\n## Parts not at risk\nP-3001: cover 35 >= 26\n## SQL used\nSELECT 1"


A5 = [("P-1001", "MCU", "yes", "S101", 12, 1200, 21, 14, 16800), ("P-1002", "MOSFET", "yes", "S101", 21, 2400, 21, 5, 12000),
      ("P-2001", "PCB", "yes", "S102", 18, 1200, 21, 8, 9600), ("P-3002", "IMU", "yes", "S103", 8, 1200, 21, 18, 21600)]


def test_constants_match_the_sop():
    assert (pc.OPS_LIMIT_INR, pc.HEAD_LIMIT_INR) == (2_500_000, 10_000_000)
    assert pc.approver_for(50_299_000) == pc.APPROVER_CFO and pc.severity_for(21) == "L2" and pc.severity_for(22) == "L3"


def test_good_scout_report_passes():
    rep, errs = ingress.check_scout_report(SCOUT_OK, URLS, TODAY)
    assert errs == [] and rep.worst_case == 21 and len(rep.events) == 2 and rep.material


def test_no_disruption_report_passes():
    rep, errs = ingress.check_scout_report(SCOUT_NONE, set(), TODAY)
    assert errs == [] and not rep.material


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.replace("https://port.example/keelung", "https://invented.example/x"), "never returned by Web Search"),
    (lambda s: s.replace("Worst-case delay: 21 days", "Worst-case delay: 14 days", 1), "worst-case delay must equal"),
    (lambda s: s.replace("2026-10-01", "2026-08-01"), "outside the last 30 days"),
    (lambda s: s.replace("## Verdict\nMATERIAL DISRUPTION FOUND", "## Verdict\nNO MATERIAL DISRUPTION FOUND"), "verdict is NO MATERIAL"),
    (lambda s: s.replace("## Verdict\nMATERIAL DISRUPTION FOUND", ""), "missing '## Verdict'"),
    (lambda s: s.replace("Delay range: 14-21 days | ", ""), "Delay range"),
])
def test_scout_mutations_are_rejected(mutate, needle):
    _, errs = ingress.check_scout_report(mutate(SCOUT_OK), URLS, TODAY)
    assert any(needle in e for e in errs), errs


def test_single_source_incident_is_unconfirmed():
    one = SCOUT_OK.replace(", https://cna.example/taiwan-ports-shut", "").replace("https://port.example/keelung", "https://reuters.example/typhoon-kaohsiung")
    _, errs = ingress.check_scout_report(one, URLS, TODAY)
    assert any("SOP 2.3" in e for e in errs)


def test_scout_size_cap():
    _, errs = ingress.check_scout_report("x" * 7000, URLS, TODAY)
    assert "under 6000" in errs[0]


def scout():
    return ingress.parse_scout_report(SCOUT_OK)


def test_a5_analyst_numbers_pass():
    assert ingress.check_analyst_report(analyst(A5), scout(), ERP) == []


@pytest.mark.parametrize("row,needle", [
    (("P-1001", "MCU", "yes", "S101", 12, 1200, 21, 14, 16000), "shortfall_units must be"),
    (("P-1001", "MCU", "yes", "S101", 12, 1200, 21, 13, 16800), "gap_days must be"),
    (("P-1001", "MCU", "yes", "S101", 15, 1200, 21, 11, 13200), "days_of_cover is 12.0"),
    (("P-3001", "Sensor", "no", "S103", 35, 1200, 21, -9, 0), "belongs under 'Parts not at risk'"),
    (("P-5001", "Cell", "yes", "S401", 25, 96000, 21, 1, 96000), "not on an affected lane"),
    (("P-9999", "Ghost", "yes", "S101", 5, 10, 21, 21, 210), "does not exist in the ERP"),
    (("P-1001", "MCU", "yes", "S102", 12, 1200, 21, 14, 16800), "primary supplier is S101"),
    (("P-1001", "MCU", "yes", "S101", 12, 1200, 14, 7, 8400), "must be the Scout's 21"),
])
def test_analyst_mutations_are_rejected(row, needle):
    errs = ingress.check_analyst_report(analyst([row]), scout(), ERP)
    assert any(needle in e for e in errs), errs


def test_malformed_table_is_reported_not_crashed():
    errs = ingress.check_analyst_report("## Exposed parts\n| P-1001 | MCU | yes |\n", scout(), ERP)
    assert "expected 9" in errs[0]
    assert "malformed" in ingress.check_analyst_report(analyst([("P-1001", "MCU", "yes", "S101", "twelve", 1200, 21, 14, 16800)]), scout(), ERP)[0]


def test_rows_listed_when_scout_found_nothing_are_rejected():
    errs = ingress.check_analyst_report(analyst(A5[:1]), ingress.parse_scout_report(SCOUT_NONE), ERP)
    assert "must be empty" in errs[0]


class Out:  # minimal TaskOutput stand-in
    def __init__(self, raw): self.raw = raw


def test_scout_guardrail_returns_feedback_for_the_agent(monkeypatch):
    import tools.search_tool as st
    monkeypatch.setattr(st, "_SEEN_URLS", set(URLS))
    ok, res = scout_guardrail(Out(SCOUT_OK.replace("2026-10-01", date.today().isoformat())))
    assert ok and res.startswith("## Disruption events")
    ok, msg = scout_guardrail(Out(SCOUT_OK.replace("https://port.example/keelung", "https://fake.example/1")))
    assert not ok and "Fix these problems" in msg


def test_analyst_guardrail_fails_closed_when_erp_is_down():
    class T: output = Out(SCOUT_OK)
    def boom(): raise ConnectionError("db down")
    ok, msg = make_analyst_guardrail(T, engine_factory=boom)(Out(analyst(A5)))
    assert not ok and "could not be reached" in msg


def test_erp_rows_with_bad_values_are_refused():
    good = dict(supplier_id="S201", part_id="P-1001", sourcing_role="APPROVED_BACKUP", unit_price_inr=452, moq=2000,
                monthly_capacity=30000, standard_lead_days=21, avl_status="APPROVED", contact_email="rfq@x.example")
    inv = [dict(part_id="P-1001", on_hand_qty=14400, daily_consumption=1200)]
    assert ingress.validate_erp_rows([good], inv)["parts"]["P-1001"]["cover"] == 12.0
    for bad in (dict(good, moq=0), dict(good, contact_email=""), dict(good, unit_price_inr=None), dict(good, avl_status="MAYBE")):
        with pytest.raises(ValueError):
            ingress.validate_erp_rows([bad], inv)
    with pytest.raises(ValueError):
        ingress.validate_erp_rows([good], [dict(part_id="P-1001", on_hand_qty=10, daily_consumption=0)])
    with pytest.raises(ValueError):
        ingress.validate_erp_rows([], inv)
