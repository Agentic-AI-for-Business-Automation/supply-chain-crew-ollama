"""SOP-SC-015 in the database: windows, reminder, escalation ladder, HOLD, authority and state machine, with a controlled clock."""
from tests.dbconf import admin_dsn
import json, os, threading
from datetime import datetime, timedelta, timezone
import psycopg2, pytest
from schemas import policy_constants as pc

ADMIN = admin_dsn()
OPS, HEAD, CFO = pc.APPROVER_OPS, pc.APPROVER_HEAD, pc.APPROVER_CFO
_n = [int(datetime.now().strftime("%H%M%S")) * 100]


@pytest.fixture()
def conn():
    try:
        c = psycopg2.connect(ADMIN, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f"admin connection unavailable: {e}")
    yield c
    c.rollback(); c.close()


def new_event(c, severity="L3", approver=CFO, action="RFQ", extra=None):
    _n[0] += 1
    eid = f"SCD-20261003-{_n[0] % 0xFFFFFF:06X}"
    payload = dict(extra or {}, event_id=eid)
    c.cursor().execute("INSERT INTO action_log (event_id, action_type, severity, total_value_inr, approver, payload) VALUES (%s,%s,%s,100,%s,%s)",
                       (eid, action, severity, approver, json.dumps(payload)))
    return eid


def row(c, eid):
    cur = c.cursor(); cur.execute("SELECT status, current_rank, required_rank, opened_at, level_started_at, deadline_at, reminded_at, escalations FROM approvals WHERE event_id=%s", (eid,))
    return cur.fetchone()


def tick(c, now):
    cur = c.cursor(); cur.execute("SELECT t_event_id, t_action, t_from_level, t_to_level FROM approval_tick(%s)", (now,))
    return cur.fetchall()


def mine(rows, eid): return [r[1:] for r in rows if r[0] == eid]


def test_seeded_policy_equals_the_constants(conn):
    cur = conn.cursor()
    cur.execute("SELECT severity, window_minutes, reminder_pct FROM approval_policy ORDER BY 1")
    assert cur.fetchall() == [(s, h * 60, pc.APPROVAL_REMINDER_PCT) for s, h in sorted(pc.APPROVAL_WINDOW_HOURS.items())]
    cur.execute("SELECT name FROM approval_levels ORDER BY rank")
    assert tuple(r[0] for r in cur.fetchall()) == pc.APPROVAL_LADDER


@pytest.mark.parametrize("sev,hours", [("L3", 4), ("L2", 8)])
def test_deadline_follows_the_severity_window(conn, sev, hours):
    eid = new_event(conn, sev, OPS)
    st, cur_rank, req, opened, started, deadline, *_ = row(conn, eid)
    assert (st, cur_rank, req) == ("PENDING", 1, 1)
    assert deadline - opened == timedelta(hours=hours)


def test_only_rfq_actions_need_approval_and_production_notices_are_created(conn):
    eid = new_event(conn, "L2", HEAD, action="MONITOR_ONLY")
    assert row(conn, eid) is None
    eid2 = new_event(conn, "L2", CFO, extra={"production_notifications": [{"part_id": "P-3002", "reason": "stock-out in 8 days"}, {"part_id": "P-1001", "reason": "x"}]})
    cur = conn.cursor()
    cur.execute("SELECT kind, recipient FROM notification_outbox WHERE event_id=%s ORDER BY id", (eid2,))
    got = cur.fetchall()
    assert got[0] == ("APPROVAL_REQUEST", CFO) and [g for g in got if g[0] == "PRODUCTION"] == [("PRODUCTION", "Production Planning")] * 2


def test_reminder_once_then_escalation_with_fresh_window_then_hold(conn):
    eid = new_event(conn, "L3", OPS)                    # window 240 min
    opened = row(conn, eid)[3]
    assert mine(tick(conn, opened + timedelta(minutes=100)), eid) == []                 # before 50%
    assert mine(tick(conn, opened + timedelta(minutes=121)), eid) == [("REMINDER", OPS, OPS)]
    assert mine(tick(conn, opened + timedelta(minutes=122)), eid) == []                 # reminder fires once
    t1 = opened + timedelta(minutes=241)
    assert mine(tick(conn, t1), eid) == [("ESCALATED", OPS, HEAD)]
    st, rank, _, _, started, deadline, reminded, esc = row(conn, eid)
    assert (st, rank, esc, reminded) == ("ESCALATED", 2, 1, None) and started == t1 and deadline == t1 + timedelta(minutes=240)
    assert mine(tick(conn, t1), eid) == []                                               # same instant: idempotent
    t2 = deadline + timedelta(seconds=1)
    assert mine(tick(conn, t2), eid) == [("ESCALATED", HEAD, CFO)]
    t3 = row(conn, eid)[5] + timedelta(seconds=1)
    assert mine(tick(conn, t3), eid) == [("HELD", CFO, CFO)]
    assert row(conn, eid)[0] == "HELD"
    assert mine(tick(conn, t3 + timedelta(days=3)), eid) == []                           # HELD is final until a human decides
    cur = conn.cursor(); cur.execute("SELECT kind FROM approval_events WHERE event_id=%s ORDER BY id", (eid,))
    assert [r[0] for r in cur.fetchall()] == ["OPENED", "REMINDER", "ESCALATED", "ESCALATED", "HELD"]


def test_cfo_level_record_goes_straight_to_hold(conn):
    eid = new_event(conn, "L2", CFO)
    opened = row(conn, eid)[3]
    assert mine(tick(conn, opened + timedelta(minutes=481)), eid) == [("HELD", CFO, CFO)]
    cur = conn.cursor(); cur.execute("SELECT count(*) FROM notification_outbox WHERE event_id=%s AND kind='HELD'", (eid,)); assert cur.fetchone()[0] == 1


def decide(c, eid, actor, decision="APPROVE", now=None):
    cur = c.cursor(); cur.execute("SELECT approval_decide(%s,%s,%s,'note',COALESCE(%s, now()))", (eid, actor, decision, now)); return cur.fetchone()[0]


def test_authority_is_never_lowered(conn):
    eid = new_event(conn, "L3", HEAD)
    assert decide(conn, eid, OPS) == "DENIED"
    assert decide(conn, eid, "Nobody") == "UNKNOWN_ACTOR"
    assert decide(conn, eid, HEAD, "MAYBE") == "BAD_DECISION"
    assert decide(conn, "SCD-20261003-FFFFF0", HEAD) == "UNKNOWN_EVENT"
    assert decide(conn, eid, CFO) == "APPROVED"                                          # higher authority may decide
    assert decide(conn, eid, CFO) == "ALREADY_DECIDED"


def test_escalated_record_can_still_be_decided_by_the_required_level(conn):
    eid = new_event(conn, "L2", OPS)
    tick(conn, row(conn, eid)[5] + timedelta(seconds=1))
    assert row(conn, eid)[0] == "ESCALATED"
    assert decide(conn, eid, OPS, "REJECT") == "REJECTED"


def test_late_human_decision_after_hold_is_possible_and_release_is_once_only(conn):
    eid = new_event(conn, "L3", CFO)
    tick(conn, row(conn, eid)[5] + timedelta(seconds=1)); assert row(conn, eid)[0] == "HELD"
    cur = conn.cursor()
    cur.execute("SELECT approval_release(%s)", (eid,)); assert cur.fetchone()[0]["reason"] == "NOT_APPROVED"      # nothing leaves while HELD
    cur.execute("SELECT approval_store_documents(%s, %s)", (eid, json.dumps([{"rfq_number": "X-RFQ01"}]))); assert cur.fetchone()[0] is True
    cur.execute("SELECT approval_store_documents(%s, %s)", (eid, json.dumps([{"rfq_number": "OTHER"}]))); assert cur.fetchone()[0] is False  # write-once
    assert decide(conn, eid, CFO) == "APPROVED"
    cur.execute("SELECT approval_release(%s)", (eid,)); out = cur.fetchone()[0]
    assert out["released"] is True and out["documents"] == [{"rfq_number": "X-RFQ01"}] and out["approved_by"] == CFO
    cur.execute("SELECT approval_release(%s)", (eid,)); assert cur.fetchone()[0]["reason"] == "ALREADY_RELEASED"


def _try(c, sql, params=None):
    cur = c.cursor(); cur.execute("SAVEPOINT s")
    try: cur.execute(sql, params)
    except psycopg2.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT s"); return str(e).split("\n")[0]
    cur.execute("RELEASE SAVEPOINT s"); return None


def test_state_machine_and_immutability(conn):
    eid = new_event(conn, "L3", OPS)
    assert "immutable" in _try(conn, "UPDATE approvals SET required_rank=3 WHERE event_id=%s", (eid,))
    assert "immutable" in _try(conn, "UPDATE approvals SET opened_at=opened_at + interval '1 minute' WHERE event_id=%s", (eid,))
    assert "never deleted" in _try(conn, "DELETE FROM approvals WHERE event_id=%s", (eid,))
    assert "append-only" in _try(conn, "DELETE FROM approval_events WHERE event_id=%s", (eid,))
    assert "append-only" in _try(conn, "UPDATE approval_events SET note='x' WHERE event_id=%s", (eid,))
    assert "violates check constraint" in _try(conn, "UPDATE approvals SET released_at=now() WHERE event_id=%s", (eid,))      # only APPROVED may be released
    assert "violates check constraint" in _try(conn, "UPDATE approvals SET status='APPROVED' WHERE event_id=%s", (eid,))       # a decision needs decided_by/at
    tick(conn, row(conn, eid)[5] + timedelta(seconds=1))
    assert row(conn, eid)[0] == "ESCALATED"
    assert "illegal approvals transition" in _try(conn, "UPDATE approvals SET status='PENDING' WHERE event_id=%s", (eid,))
    assert decide(conn, eid, OPS) == "APPROVED"
    assert "terminal" in _try(conn, "UPDATE approvals SET status='REJECTED' WHERE event_id=%s", (eid,))
    assert "terminal" in _try(conn, "UPDATE approvals SET decided_by='someone else' WHERE event_id=%s", (eid,))


def test_held_cannot_return_to_pending(conn):
    eid = new_event(conn, "L2", CFO)
    tick(conn, row(conn, eid)[5] + timedelta(seconds=1))
    assert row(conn, eid)[0] == "HELD"
    assert "illegal approvals transition" in _try(conn, "UPDATE approvals SET status='PENDING' WHERE event_id=%s", (eid,))
    assert "illegal approvals transition" in _try(conn, "UPDATE approvals SET status='ESCALATED' WHERE event_id=%s", (eid,))


def test_unknown_approver_cannot_open_an_approval(conn):
    assert "not on the approval ladder" in _try(conn, "INSERT INTO action_log (event_id, action_type, severity, total_value_inr, approver, payload) "
                                                    "VALUES ('SCD-20261003-AAAAA1','RFQ','L2',1,'The Intern','{}')")


def test_two_concurrent_ticks_escalate_only_once():
    try:
        setup = psycopg2.connect(ADMIN, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(str(e))
    setup.autocommit = True
    eid = new_event(setup, "L3", OPS)
    opened = row(setup, eid)[3]
    when = opened + timedelta(minutes=241)
    results = [None, None]; barrier = threading.Barrier(2)

    def worker(i):
        c = psycopg2.connect(ADMIN); c.autocommit = True; barrier.wait()
        results[i] = mine(tick(c, when), eid); c.close()
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(len(r) for r in results) == [0, 1]
    assert row(setup, eid)[7] == 1
    setup.close()


def test_notification_dispatch_claims_each_row_once(conn):
    new_event(conn, "L2", OPS)
    cur = conn.cursor()
    cur.execute("SELECT id FROM notify_claim(50)"); ids = [r[0] for r in cur.fetchall()]
    assert ids
    cur.execute("SELECT count(*) FROM notify_claim(50) WHERE id = ANY(%s)", (ids,)); assert cur.fetchone()[0] == 0
    cur.execute("SELECT notify_done(%s)", (ids,)); assert cur.fetchone()[0] == len(ids)


def test_changing_the_constants_is_enough_the_sync_updates_the_database(monkeypatch):
    """Editing schemas/policy_constants.py and running tools.migrate must update the stored policy (no migration edit needed)."""
    from tools import migrate
    from tools.db import get_engine, reset_engines
    reset_engines()
    try:
        eng = get_engine("migrate")
        eng.connect().close()
    except Exception as e:
        pytest.skip(f"migration connection unavailable: {e}")
    def stored():
        with get_engine("migrate").connect() as c:
            from sqlalchemy import text
            return {r[0]: (r[1], r[2]) for r in c.execute(text("SELECT severity, window_minutes, reminder_pct FROM approval_policy"))}
    try:
        monkeypatch.setitem(pc.APPROVAL_WINDOW_HOURS, "L2", 9)
        monkeypatch.setattr(pc, "APPROVAL_REMINDER_PCT", 40)
        migrate.migrate()
        assert stored() == {"L2": (540, 40), "L3": (240, 40)}
        assert migrate.migrate() == []                                  # second run changes nothing
    finally:
        monkeypatch.undo()
        migrate.migrate()                                               # restore the real values
        reset_engines()
    assert stored() == {"L2": (480, 50), "L3": (240, 50)}


def test_new_approvals_use_the_synced_window_but_open_ones_keep_their_deadline(conn, monkeypatch):
    cur = conn.cursor()
    first = new_event(conn, "L2", OPS)
    cur.execute("UPDATE approval_policy SET window_minutes = 600 WHERE severity = 'L2'")     # same transaction, rolled back by the fixture
    second = new_event(conn, "L2", OPS)
    assert row(conn, first)[5] - row(conn, first)[3] == timedelta(minutes=480)
    assert row(conn, second)[5] - row(conn, second)[3] == timedelta(minutes=600)


def test_notification_routes_validate_and_join_to_the_claimed_notifications(conn):
    cur = conn.cursor()
    cur.execute("SELECT notification_route_set('The Intern', 'a@b.co')"); assert cur.fetchone()[0] == "UNKNOWN_RECIPIENT"
    cur.execute("SELECT notification_route_set(%s, 'not-an-email')", (CFO,)); assert cur.fetchone()[0] == "BAD_EMAIL"
    cur.execute("SELECT notification_route_set(%s, '  CFO@Example.COM ')", (CFO,)); assert cur.fetchone()[0] == "OK"
    cur.execute("SELECT email FROM notification_routes WHERE recipient=%s", (CFO,)); assert cur.fetchone()[0] == "cfo@example.com"
    cur.execute("SELECT notification_route_set('SCM Alerts', 'ops@example.com')"); assert cur.fetchone()[0] == "OK"
    eid = new_event(conn, "L2", CFO, extra={"production_notifications": [{"part_id": "P-3002", "reason": "stock-out"}]})
    cur.execute("SELECT recipient, email FROM notify_claim_routed(200) WHERE event_id=%s ORDER BY id", (eid,))
    assert cur.fetchall() == [(CFO, "cfo@example.com"), ("Production Planning", None)]          # no route for Production Planning: log only
    cur.execute("SELECT notification_route_clear(%s)", (CFO,)); assert cur.fetchone()[0] == 1
    cur.execute("SELECT notification_route_clear(%s)", (CFO,)); assert cur.fetchone()[0] == 0


def test_no_email_address_is_seeded_by_the_migrations(conn):
    import glob, re
    for path in glob.glob(os.path.join(os.path.dirname(__file__), "..", "db", "migrations", "V00[45]_*.sql")):
        text = open(path, encoding="utf-8").read()
        assert not re.search(r"[A-Za-z0-9._-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", text), f"{path} contains an email address"
        assert "INSERT INTO notification_routes (recipient, email) VALUES ('" not in text           # no seeded route
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM notification_routes WHERE recipient NOT IN (SELECT name FROM approval_levels) AND recipient NOT IN ('Production Planning','Procurement','SCM Alerts')")
    assert cur.fetchone()[0] == 0
