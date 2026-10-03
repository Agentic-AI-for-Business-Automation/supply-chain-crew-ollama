"""V002: ports FK, action_log state machine, dead-letter queue claim/backoff, migration runner."""
from tests.dbconf import admin_dsn
import json, os, threading
import psycopg2, pytest

ADMIN_DSN = admin_dsn()
PAYLOAD = json.dumps({"k": "v"})


@pytest.fixture()
def conn():
    try:
        c = psycopg2.connect(ADMIN_DSN, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f"admin connection unavailable (set ERP_ADMIN_URL): {e}")
    yield c
    c.rollback()
    c.close()


def _try(c, sql, params=None):
    cur = c.cursor(); cur.execute("SAVEPOINT s")
    try:
        cur.execute(sql, params)
    except psycopg2.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT s")
        return str(e).split("\n")[0]
    cur.execute("RELEASE SAVEPOINT s")
    return None


def _new_event(c, eid, status="PENDING"):
    c.cursor().execute("INSERT INTO action_log (event_id, action_type, severity, total_value_inr, approver, payload, status) "
                       "VALUES (%s,'RFQ','L2',100,'Chief Financial Officer',%s,%s)", (eid, PAYLOAD, status))


def test_unknown_port_is_rejected_by_foreign_key(conn):
    err = _try(conn, "UPDATE purchase_orders SET origin_port='Kaohsiung Port' WHERE po_id='PO-26-0412'")
    assert err and "foreign key" in err.lower()
    assert _try(conn, "UPDATE suppliers SET export_port='Atlantis' WHERE supplier_id='S101'")


def test_legal_transitions_work(conn):
    _new_event(conn, "SCD-20261003-AB0001")
    cur = conn.cursor()
    assert _try(conn, "UPDATE action_log SET status='QUEUED', attempts=1 WHERE event_id='SCD-20261003-AB0001'") is None
    assert _try(conn, "UPDATE action_log SET status='DELIVERED' WHERE event_id='SCD-20261003-AB0001'") is None
    cur.execute("SELECT status, attempts FROM action_log WHERE event_id='SCD-20261003-AB0001'")
    assert cur.fetchone() == ("DELIVERED", 1)


@pytest.mark.parametrize("start,target", [("DELIVERED", "PENDING"), ("DELIVERED", "QUEUED"), ("DEAD", "DELIVERED"),
                                           ("REJECTED", "DELIVERED"), ("QUEUED", "PENDING")])
def test_illegal_transitions_are_refused(conn, start, target):
    _new_event(conn, "SCD-20261003-AB0002", status=start)
    err = _try(conn, "UPDATE action_log SET status=%s WHERE event_id='SCD-20261003-AB0002'", (target,))
    assert err and "illegal action_log transition" in err


def test_record_content_is_immutable_and_rows_cannot_be_deleted(conn):
    _new_event(conn, "SCD-20261003-AB0003")
    assert "immutable" in _try(conn, "UPDATE action_log SET payload='{\"x\":1}' WHERE event_id='SCD-20261003-AB0003'")
    assert "immutable" in _try(conn, "UPDATE action_log SET approver='Operations Manager - Procurement' WHERE event_id='SCD-20261003-AB0003'")
    assert "append-only" in _try(conn, "DELETE FROM action_log WHERE event_id='SCD-20261003-AB0003'")
    err = _try(conn, "TRUNCATE action_log")
    assert err and ("append-only" in err or "foreign key" in err)       # refused either by the trigger or by approvals referencing it


def test_dead_letter_requires_event_and_payload_when_retryable(conn):
    assert _try(conn, "INSERT INTO dead_letter (source, retryable) VALUES ('tool-delivery', TRUE)")
    assert _try(conn, "INSERT INTO dead_letter (source, errors) VALUES ('n8n-validate', '[\"x\"]')") is None


def test_claim_release_backoff_and_dead_after_six_attempts(conn):
    cur = conn.cursor()
    _new_event(conn, "SCD-20261003-AB0004", status="QUEUED")
    cur.execute("INSERT INTO dead_letter (source,event_id,payload,retryable) VALUES ('tool-delivery','SCD-20261003-AB0004',%s,TRUE) RETURNING id", (PAYLOAD,))
    did = cur.fetchone()[0]
    for attempt in range(1, 7):
        cur.execute("UPDATE dead_letter SET next_retry_at = now() - interval '1 second' WHERE id=%s", (did,))
        cur.execute("SELECT id, attempts FROM dlq_claim(50) WHERE id=%s", (did,))
        assert cur.fetchone() == (did, attempt)
        cur.execute("SELECT dlq_release(%s, FALSE, 'connection refused')", (did,))
        outcome = cur.fetchone()[0]
        assert outcome == ("DEAD" if attempt == 6 else "OPEN")
    cur.execute("SELECT status FROM action_log WHERE event_id='SCD-20261003-AB0004'")
    assert cur.fetchone()[0] == "DEAD"
    cur.execute("SELECT count(*) FROM dlq_claim(50) WHERE id=%s", (did,))
    assert cur.fetchone()[0] == 0


def test_backoff_grows_exponentially_and_is_capped(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO dead_letter (source,event_id,payload,retryable,attempts,status) "
                "VALUES ('tool-delivery','SCD-20261003-AB0005',%s,TRUE,3,'RETRYING') RETURNING id", (PAYLOAD,))
    did = cur.fetchone()[0]
    cur.execute("SELECT dlq_release(%s, FALSE, 'x')", (did,))
    cur.execute("SELECT extract(epoch FROM next_retry_at - now())/60 FROM dead_letter WHERE id=%s", (did,))
    assert 7.5 < cur.fetchone()[0] <= 8.01            # 2^3 minutes


def test_two_workers_never_claim_the_same_row():
    try:
        setup = psycopg2.connect(ADMIN_DSN, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(str(e))
    setup.autocommit = True
    cur = setup.cursor()
    ids = []
    for i in range(20):      # the oldest retry times in the table: dlq_claim orders by next_retry_at, so these are claimed first and
        cur.execute("INSERT INTO dead_letter (source,event_id,payload,retryable,status,next_retry_at) "    # nobody else's queue rows are touched
                    "VALUES ('tool-delivery',%s,%s,TRUE,'OPEN', now() - interval '10 years' + %s * interval '1 second') RETURNING id",
                    (f"SCD-20261003-CC{i:04d}", PAYLOAD, i)); ids.append(cur.fetchone()[0])
    got: list[list[int]] = [[], []]
    barrier = threading.Barrier(2)

    def worker(n):
        c = psycopg2.connect(ADMIN_DSN); c.autocommit = True; k = c.cursor()
        barrier.wait()
        k.execute("SELECT id FROM dlq_claim(10)"); got[n] = [r[0] for r in k.fetchall() if r[0] in ids]; c.close()
    ts = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    try:
        assert set(got[0]).isdisjoint(got[1]) and len(got[0]) + len(got[1]) == 20
    finally:
        cur.execute("DELETE FROM dead_letter WHERE id = ANY(%s)", (ids,))
        setup.close()


def test_migration_runner_is_idempotent_and_records_versions():
    from tools import migrate
    try:
        migrate.migrate()
        assert migrate.migrate() == []
    except Exception as e:
        if "connect" in str(e).lower():
            pytest.skip(str(e))
        raise
    c = psycopg2.connect(ADMIN_DSN); cur = c.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY 1")
    assert [r[0] for r in cur.fetchall()][:2] == ["V001", "V002"]
    c.close()


def test_views_exist_and_ro_role_can_read_them():
    dsn = os.getenv("DATABASE_URL", "").replace("+psycopg2", "")
    if "scm_ro" not in dsn:
        pytest.skip("DATABASE_URL does not use scm_ro")
    c = psycopg2.connect(dsn); cur = c.cursor()
    cur.execute("SELECT count(*) FROM v_lane_exposure WHERE port IN ('Kaohsiung','Keelung')"); assert cur.fetchone()[0] == 5
    cur.execute("SELECT open_po_qty FROM v_part_pipeline WHERE part_id='P-1001'"); assert cur.fetchone()[0] == 36000
    cur.execute("SELECT count(*) FROM ports"); assert cur.fetchone()[0] == 8
    c.close()
