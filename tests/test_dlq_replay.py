"""Integration: the real action_log / dead_letter tables with a scripted HTTP layer (needs the ERP database)."""
from tests.dbconf import admin_dsn
import copy, json, os
import psycopg2, pytest
import requests
from sqlalchemy import text

from tools import dlq
from tools.db import get_engine, reset_engines

ADMIN = admin_dsn()
BASE = {"action_type": "RFQ", "severity": "L2", "approval_authority": "Chief Financial Officer",
        "rfqs": [{"est_value_inr": 9944000}]}


@pytest.fixture()
def db():
    reset_engines()
    try:
        get_engine("audit").connect().close()
    except Exception as e:
        pytest.skip(f"audit database unavailable: {e}")
    ids = []
    yield ids
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("DELETE FROM dead_letter WHERE event_id = ANY(%s)", (ids,))
        # action_log is append-only by design: test rows stay but are marked terminal so they never replay
    reset_engines()


def payload(i, ids):
    eid = f"SCD-20261003-EE{i:04d}"
    ids.append(eid)
    return dict(copy.deepcopy(BASE), event_id=eid)


class R:
    def __init__(self, code=200): self.status_code, self.text = code, "{}"
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


def status(eid):
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("SELECT a.status, a.attempts, d.status, d.attempts FROM action_log a LEFT JOIN dead_letter d ON d.event_id=a.event_id WHERE a.event_id=%s", (eid,))
        return k.fetchone()


def make_ids():
    import time
    return int(time.time()) % 9000


def test_failed_delivery_is_queued_then_replayed_and_resolved(db, monkeypatch):
    p = payload(make_ids(), db)
    assert dlq.record_intent(p) == "PENDING"
    assert dlq.record_intent(p) == "PENDING"                       # idempotent insert, no overwrite
    dlq.queue_for_retry(p, "connection refused")
    assert status(p["event_id"])[:3] == ("QUEUED", 1, "OPEN")
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("UPDATE dead_letter SET next_retry_at = now() - interval '1 second' WHERE event_id=%s", (p["event_id"],))
    posted = []
    monkeypatch.setattr(dlq.requests, "post", lambda url, **kw: posted.append(kw["json"]["event_id"]) or R(200))
    out = dlq.replay(batch=50)
    assert p["event_id"] in posted and out["resolved"] >= 1
    assert status(p["event_id"])[0] == "DELIVERED" and status(p["event_id"])[2] == "RESOLVED"


def test_temporary_failure_backs_off_and_permanent_failure_dies(db, monkeypatch):
    soft, hard = payload(make_ids() + 1, db), payload(make_ids() + 2, db)
    for p in (soft, hard):
        dlq.record_intent(p); dlq.queue_for_retry(p, "x")
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("UPDATE dead_letter SET next_retry_at = now() - interval '1 second' WHERE event_id = ANY(%s)", ([soft["event_id"], hard["event_id"]],))
    monkeypatch.setattr(dlq.requests, "post", lambda url, **kw: R(503 if kw["json"]["event_id"] == soft["event_id"] else 422))
    dlq.replay(batch=50)
    assert status(soft["event_id"])[2:] == ("OPEN", 1)
    st = status(hard["event_id"])
    assert st[0] == "DEAD" and st[2] == "DEAD"
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("SELECT extract(epoch FROM next_retry_at - now()) FROM dead_letter WHERE event_id=%s", (soft["event_id"],))
        assert 100 < k.fetchone()[0] < 130                          # 2^1 minutes
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:             # leave nothing replayable behind
        k.execute("UPDATE dead_letter SET status='DEAD' WHERE event_id=%s", (soft["event_id"],))


def test_replay_survives_an_unreachable_database(monkeypatch):
    monkeypatch.setenv("AUDIT_DATABASE_URL", "postgresql://scm_audit:scm_audit@localhost:1/erp")
    reset_engines()
    try:
        assert dlq.replay() == {"adopted": 0, "resolved": 0, "retry": 0, "dead": 0}
    finally:
        monkeypatch.undo(); reset_engines()


def test_spill_file_is_adopted_into_the_queue(db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = payload(make_ids() + 3, db)
    dlq.spill_to_file(p)
    monkeypatch.setattr(dlq.requests, "post", lambda *a, **k: R(503))
    out = dlq.replay(batch=50)
    assert out["adopted"] == 1 and not list((tmp_path / "outbox").glob("*.json"))
    assert status(p["event_id"])[0] == "QUEUED"
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("UPDATE dead_letter SET status='DEAD' WHERE event_id=%s", (p["event_id"],))


def test_pool_recovers_after_connections_are_killed(db):
    eng = get_engine("audit")
    with eng.connect() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='scm_audit' AND pid <> pg_backend_pid()")
    with eng.connect() as c:                                        # pool_pre_ping must replace the dead connection
        assert c.execute(text("SELECT 1")).scalar() == 1
