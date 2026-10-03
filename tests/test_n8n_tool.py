import json
import tools.n8n_tool as nt

GOOD = {"action_type": "RFQ", "severity": "L2", "disruption_summary": "Typhoon closes Taiwan ports", "disruption_location": "Taiwan",
        "sources": ["https://example.com/typhoon"],
        "affected_parts": [{"part_id": "P-1001", "part_name": "MCU", "days_of_cover": 12,
                            "projected_delay_days": 21, "shortfall_units": 16800}],
        "rfqs": [{"supplier_id": "S201", "supplier_name": "Penang Semicon Sdn. Bhd.", "part_id": "P-1001",
                  "quantity": 22000, "unit_price_inr": 452, "est_value_inr": 9944000,
                  "required_within_days": 21, "justification": "SOP-SC-014 3.2 trigger rule"}],
        "approval_authority": "Head of Supply Chain Management",
        "policy_citations": [{"document": "SOP-SC-014_Supply_Disruption_Response.pdf", "page": 1, "clause": "3.2"}]}

def test_invalid_payload_returns_validation_error():
    bad = dict(GOOD, rfqs=[])     # RFQ without lines
    assert nt.trigger_n8n.run(payload_json=json.dumps(bad)).startswith("VALIDATION ERROR")

def test_missing_citations_rejected():
    bad = dict(GOOD, policy_citations=[])
    assert nt.trigger_n8n.run(payload_json=json.dumps(bad)).startswith("VALIDATION ERROR")

def test_success(monkeypatch):
    class R:
        status_code, text = 200, '{"status":"RECEIVED"}'
        def raise_for_status(self): pass
    monkeypatch.setattr(nt.requests, "post", lambda *a, **k: R())
    out = nt.trigger_n8n.run(payload_json="```json\n" + json.dumps(GOOD) + "\n```")
    assert out.startswith("SUCCESS") and nt.STATE["sent"]

def test_outbox_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(nt.time, "sleep", lambda s: None)
    def boom(*a, **k): raise ConnectionError("down")
    monkeypatch.setattr(nt.requests, "post", boom)
    out = nt.trigger_n8n.run(payload_json=json.dumps(GOOD))
    assert "queued locally" in out and list((tmp_path / "outbox").glob("*.json"))
