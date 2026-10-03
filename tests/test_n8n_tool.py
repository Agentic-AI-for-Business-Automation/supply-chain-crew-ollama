import copy, json
import pytest
import requests
import tools.n8n_tool as nt
from tools import dlq

SOP = "SOP-SC-014_Supply_Disruption_Response.pdf"
FAKE_ERP = {("S201", "P-1001"): {"role": "APPROVED_BACKUP", "price": 452.0, "moq": 2000, "capacity": 30000,
                                 "avl": "APPROVED", "email": "rfq@penang-semicon.example", "lead_days": 21},
            "parts": {"P-1001": {"cover": 12.0, "daily": 1200}}}
GOOD = {"action_type": "RFQ", "severity": "L2", "disruption_summary": "Typhoon closes Taiwan ports", "disruption_location": "Taiwan",
        "sources": ["https://example.com/typhoon"],
        "affected_parts": [{"part_id": "P-1001", "part_name": "MCU", "days_of_cover": 12,
                            "projected_delay_days": 21, "shortfall_units": 16800}],
        "rfqs": [{"supplier_id": "S201", "supplier_name": "Penang Semicon Sdn. Bhd.", "part_id": "P-1001",
                  "quantity": 22000, "unit_price_inr": 452, "est_value_inr": 9944000,
                  "required_within_days": 21, "justification": "SOP-SC-014 3.2 trigger rule"}],
        "approval_authority": "Head of Supply Chain Management",
        "policy_citations": [{"document": SOP, "page": 1, "clause": "3.2"}]}


class Resp:
    def __init__(self, code=200, text='{"status":"RECEIVED"}'):
        self.status_code, self.text = code, text
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)
    def json(self):
        return json.loads(self.text)


class Fake:
    """Records every database interaction instead of touching a database."""
    def __init__(self): self.intent, self.status, self.queued = "PENDING", [], []


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(nt.time, "sleep", lambda s: None)
    monkeypatch.setattr(nt, "_load_erp", lambda: copy.deepcopy(FAKE_ERP))
    monkeypatch.setattr(nt, "_retrieved", lambda: {(SOP, 1)})
    f = Fake()
    monkeypatch.setattr(dlq, "record_intent", lambda payload: f.intent)
    monkeypatch.setattr(dlq, "set_status", lambda eid, new, error=None, only_from=("PENDING", "QUEUED"): f.status.append(new))
    monkeypatch.setattr(dlq, "queue_for_retry", lambda payload, err: f.queued.append(err) or 42)
    nt.STATE.update(sent=False, queued=False, last_event_id=None)
    return f


def run(payload):
    return nt.trigger_n8n.run(payload_json=payload if isinstance(payload, str) else json.dumps(payload))


def post_to(monkeypatch, fn):
    monkeypatch.setattr(dlq.requests, "post", fn)


def test_invalid_payload_returns_validation_error():
    assert run(dict(GOOD, rfqs=[])).startswith("VALIDATION ERROR")


def test_missing_citations_rejected():
    assert run(dict(GOOD, policy_citations=[])).startswith("VALIDATION ERROR")


def test_success_sends_token_and_erp_recipient(monkeypatch, isolated):
    sent = []
    monkeypatch.setenv("N8N_WEBHOOK_TOKEN", "s3cret")
    post_to(monkeypatch, lambda url, **k: sent.append(k) or Resp())
    out = run("```json\n" + json.dumps(dict(GOOD, rfqs=[dict(GOOD["rfqs"][0], supplier_email="attacker@evil.test")])) + "\n```")
    assert out.startswith("SUCCESS") and nt.STATE["sent"] and "DELIVERED" in isolated.status
    assert sent[0]["json"]["rfqs"][0]["supplier_email"] == "rfq@penang-semicon.example"
    assert sent[0]["headers"]["X-SCM-Token"] == "s3cret" and sent[0]["headers"]["Idempotency-Key"].startswith("SCD-")


@pytest.mark.parametrize("raw", ["Here is the payload:\n" + json.dumps(GOOD) + "\nHope that helps!", GOOD])
def test_prose_wrapped_json_and_dict_input_do_not_crash(monkeypatch, raw):
    post_to(monkeypatch, lambda *a, **k: Resp())
    assert nt.trigger_n8n.func(raw).startswith("SUCCESS")


def test_garbage_input_returns_validation_error():
    assert run("I could not build the payload").startswith("VALIDATION ERROR")
    assert nt.trigger_n8n.func(None).startswith("VALIDATION ERROR")


def test_second_call_is_blocked_and_posts_once(monkeypatch):
    calls = []
    post_to(monkeypatch, lambda *a, **k: calls.append(1) or Resp())
    run(GOOD)
    assert run(GOOD).startswith("ALREADY SENT") and len(calls) == 1


def test_same_content_gives_same_event_id(monkeypatch):
    ids = []
    post_to(monkeypatch, lambda url, **k: ids.append(k["json"]["event_id"]) or Resp())
    run(GOOD); nt.STATE.update(sent=False, queued=False)
    run(GOOD)
    assert ids[0] == ids[1]


@pytest.mark.parametrize("existing,needle", [("DELIVERED", "DUPLICATE"), ("QUEUED", "ALREADY QUEUED"), ("DEAD", "already recorded as DEAD")])
def test_existing_audit_state_prevents_a_resend(monkeypatch, isolated, existing, needle):
    isolated.intent = existing
    calls = []
    post_to(monkeypatch, lambda *a, **k: calls.append(1) or Resp())
    assert needle in run(GOOD) and not calls


def test_http_4xx_rejection_is_not_retried_or_queued(monkeypatch, isolated):
    calls = []
    post_to(monkeypatch, lambda *a, **k: calls.append(1) or Resp(422, '{"errors":["approval_authority must be X"]}'))
    out = run(GOOD)
    assert out.startswith("ERROR: n8n rejected") and "approval_authority must be X" in out and len(calls) == 1
    assert isolated.status == ["REJECTED"] and not isolated.queued and not nt.STATE["queued"]


def test_auth_failure_is_queued_not_rejected(monkeypatch, isolated):
    calls = []
    post_to(monkeypatch, lambda *a, **k: calls.append(1) or Resp(403, "forbidden"))
    out = run(GOOD)
    assert len(calls) == 3 and "retried automatically" in out and isolated.queued and nt.STATE["queued"]


def test_5xx_is_retried_then_queued_in_the_database(monkeypatch, isolated, tmp_path):
    calls = []
    post_to(monkeypatch, lambda *a, **k: calls.append(1) or Resp(500, "boom"))
    out = run(GOOD)
    assert len(calls) == 3 and "dead_letter #42" in out and nt.STATE["queued"] and not nt.STATE["sent"]
    assert not (tmp_path / "outbox").exists()


def test_when_n8n_and_database_are_both_down_payload_spills_to_disk(monkeypatch, tmp_path):
    def db_down(payload): raise RuntimeError("could not connect postgresql://u:pw@h/erp")
    monkeypatch.setattr(dlq, "record_intent", db_down)
    def boom(*a, **k): raise ConnectionError("down")
    post_to(monkeypatch, boom)
    out = run(GOOD)
    assert "queued locally" in out and list((tmp_path / "outbox").glob("*.json")) and not list((tmp_path / "outbox").glob("*.tmp"))
    assert nt.STATE["queued"] and "pw" not in out


def test_duplicate_response_is_reported_to_the_agent(monkeypatch):
    post_to(monkeypatch, lambda *a, **k: Resp(200, '{"status":"DUPLICATE"}'))
    assert run(GOOD).startswith("DUPLICATE")


def test_erp_unreachable_is_fail_closed(monkeypatch):
    def down(): raise RuntimeError("could not connect postgresql://scm:scm@localhost/erp")
    monkeypatch.setattr(nt, "_load_erp", down)
    out = run(GOOD)
    assert out.startswith("ERROR: cannot verify") and "scm:scm" not in out and not nt.STATE["sent"]


def test_citation_not_retrieved_is_rejected(monkeypatch):
    monkeypatch.setattr(nt, "_retrieved", lambda: set())
    assert "never returned by 'SOP Search'" in run(GOOD)
