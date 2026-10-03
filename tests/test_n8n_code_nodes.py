"""Unit tests for the n8n Code-node JavaScript (validation, approval decisions, email routing). Skipped when node is missing."""
import copy
import pytest
from tests.js_harness import run_node

SOP = "SOP-SC-014_Supply_Disruption_Response.pdf"
GOOD = {"action_type": "RFQ", "severity": "L2", "event_id": "SCD-20261003-ABCDEF", "company": "Aravalli Mobility Pvt Ltd",
        "rfqs": [{"supplier_id": "S201", "supplier_name": "Penang Semicon", "supplier_email": "rfq@penang.example", "part_id": "P-1001",
                  "quantity": 22000, "unit_price_inr": 452, "est_value_inr": 9944000, "required_within_days": 21, "justification": "cover below buffer"}],
        "approval_authority": "Head of Supply Chain Management", "policy_citations": [{"document": SOP, "page": 1, "clause": "3.2"}]}


def validate(payload):
    return run_node("validate_build.js", [{"body": payload}])[0]


def test_valid_payload_is_built_with_server_side_totals_and_approver():
    out = validate(GOOD)
    assert out["ok"] and out["rfqs_generated"] == 1 and out["total_rfq_value_raw"] == 9944000
    assert out["routed_for_approval_to"] == "Head of Supply Chain Management"
    assert out["rfq_documents"][0]["to"] == "rfq@penang.example" and "payload_json" in out


@pytest.mark.parametrize("total_qty,price,approver", [(5000, 500, "Operations Manager - Procurement"),      # 25,00,000 exactly
                                                       (5001, 500, "Head of Supply Chain Management"),      # one rupee-step over
                                                       (20000, 500, "Head of Supply Chain Management"),     # 1,00,00,000 exactly
                                                       (20001, 500, "Chief Financial Officer")])
def test_approver_ladder_boundaries(total_qty, price, approver):
    p = copy.deepcopy(GOOD); p["rfqs"][0].update(quantity=total_qty, unit_price_inr=price, est_value_inr=total_qty * price)
    assert validate(dict(p, approval_authority=approver))["ok"]
    wrong = next(a for a in ("Operations Manager - Procurement", "Head of Supply Chain Management", "Chief Financial Officer") if a != approver)
    bad = validate(dict(p, approval_authority=wrong))
    assert not bad["ok"] and any("approval_authority must be" in e for e in bad["errors"])


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["rfqs"][0].update(quantity=-5), "quantity must be a number >= 1"),
    (lambda p: p["rfqs"][0].update(quantity="22,000"), "quantity must be a number"),
    (lambda p: p["rfqs"][0].update(quantity=1.5), "(integer)"),
    (lambda p: p["rfqs"][0].update(supplier_email="not-an-email"), "supplier_email invalid"),
    (lambda p: p["rfqs"][0].update(supplier_email=""), "supplier_email invalid"),
    (lambda p: p["rfqs"][0].update(est_value_inr=1), "est_value_inr != quantity x unit_price"),
    (lambda p: p["rfqs"][0].update(supplier_id="X1"), "supplier_id invalid"),
    (lambda p: p.update(rfqs=[]), "RFQ action needs at least one"),
    (lambda p: p.update(action_type="BUY_NOW"), "action_type 'BUY_NOW' invalid"),
    (lambda p: p.update(severity="L9"), "severity must be"),
    (lambda p: p.update(event_id="oops"), "event_id missing or malformed"),
    (lambda p: p.update(approval_authority=None), "approval_authority must be"),
    (lambda p: p.update(rfqs="not a list"), "rfqs must be an array"),
])
def test_invalid_payloads_are_rejected_with_a_reason(mutate, needle):
    p = copy.deepcopy(GOOD); mutate(p)
    out = validate(p)
    assert out["ok"] is False and any(needle in e for e in out["errors"]), out["errors"]
    assert out["status"] == "REJECTED" and "payload_json" in out


@pytest.mark.parametrize("body", [None, [], "text", 7])
def test_non_object_bodies_never_crash_the_validator(body):
    out = run_node("validate_build.js", [{"body": body}])[0]
    assert out["ok"] is False and out["errors"]


def test_line_breaks_cannot_be_injected_into_email_fields():
    p = copy.deepcopy(GOOD); p["rfqs"][0]["supplier_name"] = "Evil\r\nBcc: attacker@evil.test"; p["rfqs"][0]["justification"] = "line1\nline2"
    d = validate(p)["rfq_documents"][0]
    assert "\r" not in d["subject"] and "\n" not in d["subject"]
    assert "\r" not in d["body"] and d["body"].startswith("Dear Evil Bcc: attacker@evil.test,\n\n")      # the break became a space: no header injection
    assert "Reason: line1 line2" in d["body"]


DEC = {"event_id": "SCD-20261003-ABCDEF", "approver_level": "Chief Financial Officer", "decision": "APPROVE", "note": " ok\nthanks "}


def test_decision_validation():
    out = run_node("validate_decision.js", [{"body": DEC}])[0]
    assert out["ok"] and out["note"] == "ok thanks"
    for bad, needle in [(dict(DEC, decision="MAYBE"), "decision must be"), (dict(DEC, approver_level="The Intern"), "approver_level must be"),
                        (dict(DEC, event_id="x"), "event_id missing"), (dict(DEC, note="x" * 501), "note is longer")]:
        o = run_node("validate_decision.js", [{"body": bad}])[0]
        assert o["ok"] is False and any(needle in e for e in o["errors"])
    assert run_node("validate_decision.js", [{"body": None}])[0]["ok"] is False


@pytest.mark.parametrize("outcome,http", [("APPROVED", 200), ("REJECTED", 200), ("DENIED", 403), ("UNKNOWN_EVENT", 404),
                                          ("ALREADY_DECIDED", 409), ("UNKNOWN_ACTOR", 422), ("BAD_DECISION", 422), ("weird", 500)])
def test_decision_outcomes_map_to_http_codes(outcome, http):
    out = run_node("shape_decision.js", [{"outcome": outcome, "release": {"released": True, "documents": [{"x": 1}], "approved_by": "Chief Financial Officer"}}],
                   refs={"Validate decision": [{"event_id": "SCD-20261003-ABCDEF", "approver_level": "Chief Financial Officer"}]})[0]
    assert out["http"] == http
    if outcome == "APPROVED":
        assert out["body"]["rfq_documents"] == [{"x": 1}] and out["body"]["released"] is True


def test_decision_accepts_release_delivered_as_a_json_string():
    out = run_node("shape_decision.js", [{"outcome": "APPROVED", "release": '{"released": false, "reason": "ALREADY_RELEASED"}'}],
                   refs={"Validate decision": [{"event_id": "E", "approver_level": "L"}]})[0]
    assert out["http"] == 200 and out["body"]["released"] is False and out["body"]["release_note"] == "ALREADY_RELEASED"


ROWS = [{"id": 1, "kind": "APPROVAL_REQUEST", "event_id": "SCD-1", "recipient": "Chief Financial Officer", "subject": "Approval needed", "body": "Decide.", "attempts": 1, "email": "cfo@example.com"},
        {"id": 2, "kind": "PRODUCTION", "event_id": "SCD-1", "recipient": "Production Planning", "subject": "Stock-out risk: P-3002", "body": "x\ny", "attempts": 1, "email": None},
        {"id": 3, "kind": "HELD", "event_id": "SCD-1", "recipient": "Chief Financial Officer", "subject": "HELD", "body": "b", "attempts": 5, "email": "broken-address"}]


def fmt():
    return run_node("format_notifications.js", ROWS)[0]


def test_notifications_are_formatted_and_only_valid_addresses_are_routed():
    out = fmt()
    assert out["count"] == 3 and out["ids"] == [1, 2, 3]
    by = {m["id"]: m for m in out["messages"]}
    assert by[1]["email"] == "cfo@example.com" and by[2]["email"] is None and by[3]["email"] is None
    assert by[1]["subject"].startswith("[SCM] APPROVAL NEEDED:") and "\n" not in by[2]["text"]


def test_split_creates_one_item_per_routed_message_or_a_skip_marker():
    items = run_node("split_messages.js", [fmt()])
    assert [i["email"] for i in items] == ["cfo@example.com"] and items[0]["skip"] is False
    assert run_node("split_messages.js", [{"messages": [m for m in fmt()["messages"] if not m["email"]]}]) == [{"skip": True}]
    assert run_node("split_messages.js", [{"messages": []}]) == [{"skip": True}]


def collect(sent):
    refs = {"Format notifications": [fmt()]}
    if sent is not None:
        refs["Send email"] = sent
    return run_node("collect_results.js", [{"skip": False}], refs=refs)[0]


def test_collect_marks_sent_and_unrouted_messages_finished():
    out = collect([{"id": "gmail-abc", "threadId": "t"}])
    assert sorted(out["ids"]) == [1, 2, 3] and out["emailed"] == 1 and out["logged_only"] == 2 and out["failed"] == []


def test_collect_keeps_a_failed_send_for_retry_and_gives_up_after_five_attempts():
    msgs = fmt(); msgs["messages"][0]["email"] = "cfo@example.com"; msgs["messages"][0]["attempts"] = 2
    ctx = {"Format notifications": [msgs], "Send email": [{"error": "invalid_grant"}]}
    out = run_node("collect_results.js", [{"skip": False}], refs=ctx)[0]
    assert 1 not in out["ids"] and out["failed"] == [{"id": 1, "gave_up": False}] and out["emailed"] == 0
    msgs["messages"][0]["attempts"] = 5
    out = run_node("collect_results.js", [{"skip": False}], refs=ctx)[0]
    assert 1 in out["ids"] and out["failed"] == [{"id": 1, "gave_up": True}]


def test_collect_works_when_nothing_was_sent_at_all():
    out = run_node("collect_results.js", [{"skip": True}], refs={"Format notifications": [{"messages": [m for m in fmt()["messages"] if not m["email"]]}]})[0]
    assert sorted(out["ids"]) == [2, 3] and out["emailed"] == 0
    out = run_node("collect_results.js", [{"skip": True}], refs={"Format notifications": [{"messages": []}]})[0]
    assert out == {"ids": [], "emailed": 0, "logged_only": 0, "failed": []}


def test_alert_mail_is_built_only_with_a_route():
    err = {"errors": '["Main workflow / Claim event: connection refused"]', "execution_url": "http://localhost:5690/execution/1"}
    out = run_node("build_alert_mail.js", [{"email": "ops@example.com"}], refs={"Format error": [err]})[0]
    assert out["skip"] is False and out["email"] == "ops@example.com" and "connection refused" in out["plain"]
    assert run_node("build_alert_mail.js", [{}], refs={"Format error": [err]}) == [{"skip": True}]
    assert run_node("build_alert_mail.js", [{"email": "junk"}], refs={"Format error": [err]}) == [{"skip": True}]
