"""Live resilience checks against the running stack (database + n8n). Exit code 0 only if every check passes.
Run: python scripts/chaos_check.py   (needs docker, .env with the usual variables)"""
import concurrent.futures as cf, json, os, subprocess, sys, time
import psycopg2, requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _compose import container as _container
N8N = _container("n8n", "N8N_CONTAINER", "n8n")
URL = os.getenv("N8N_WEBHOOK_URL", "http://127.0.0.1:5678/webhook/supply-chain-rfq")
TOKEN = os.getenv("N8N_WEBHOOK_TOKEN", "")
from tests.dbconf import admin_dsn
ADMIN = admin_dsn()
SOP = "SOP-SC-014_Supply_Disruption_Response.pdf"
RESULTS: list[tuple[str, bool, str]] = []
RUN = time.strftime("%H%M%S")          # makes every payload unique, so the idempotency guard does not mask a re-run


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def sql(q, params=None):
    with psycopg2.connect(ADMIN) as c, c.cursor() as k:
        k.execute(q, params)
        return k.fetchall() if k.description else None


def rfq(s, name, part, qty, price, days):
    return dict(supplier_id=s, supplier_name=name, part_id=part, quantity=qty, unit_price_inr=price, est_value_inr=qty * price,
                required_within_days=days, justification=f"Cover below delay 21d + 5d buffer (SOP-SC-014 3.2) for {part}")


def a5_payload(tag: str = ""):
    parts = [("P-1001", "32-bit Automotive MCU", 12, 16800), ("P-1002", "Power MOSFET Module", 21, 12000),
             ("P-2001", "BMS PCB Assembly", 18, 9600), ("P-3002", "6-axis IMU Sensor Module", 8, 21600)]
    return dict(action_type="RFQ", severity="L2", disruption_summary=f"Typhoon closes Kaohsiung and Keelung ports; 14-21 day delays {tag}".strip(),
                disruption_location="Kaohsiung / Keelung, Taiwan", sources=["https://example.com/typhoon-kaohsiung-closure"],
                affected_parts=[dict(part_id=p, part_name=n, days_of_cover=c, projected_delay_days=21, shortfall_units=s) for p, n, c, s in parts],
                rfqs=[rfq("S201", "Penang Semicon Sdn. Bhd.", "P-1001", 22000, 452, 21), rfq("S201", "Penang Semicon Sdn. Bhd.", "P-1002", 15000, 655, 21),
                      rfq("S202", "Saigon Circuit Works JSC", "P-2001", 12000, 930, 24), rfq("S301", "Bengaluru Embedded Systems", "P-3002", 26000, 745, 14)],
                contingency_actions=["Raise safety stock by 30% (SOP 4.2)", "Notify Production Planning: P-3002 stock-out in 8 days (SOP 4.5)"],
                approval_authority="Chief Financial Officer",
                policy_citations=[dict(document=SOP, page=1, clause="3.2"), dict(document=SOP, page=2, clause="5.1")])


def wait_hook(timeout=120):
    """After an n8n restart wait until ALL token-protected webhooks answer (they register one by one)."""
    urls = [URL, URL.replace("supply-chain-rfq", "supply-chain-approval"), URL.replace("supply-chain-rfq", "supply-chain-tick")]
    deadline = time.time() + timeout
    pending = set(urls)
    while pending and time.time() < deadline:
        for u in list(pending):
            try:
                if requests.post(u, json={}, timeout=3).status_code in (401, 403):
                    pending.discard(u)
            except Exception:
                pass
        time.sleep(2)
    return not pending


def prime_rag():
    from tools import rag_tool
    rag_tool.build_index()
    for q in ("clause 3.2 RFQ trigger", "approval matrix section 5"):
        rag_tool.sop_search.run(query=q)


def main() -> int:
    from tools import dlq, n8n_tool
    prime_rag()
    H = {"X-SCM-Token": TOKEN, "Content-Type": "application/json"}

    # 1. authentication
    check("no token is refused", requests.post(URL, json={}, timeout=5).status_code == 403)
    check("wrong token is refused", requests.post(URL, json={}, headers={**H, "X-SCM-Token": "nope"}, timeout=5).status_code == 403)

    # 2. concurrency: one event id, 20 parallel posts -> exactly one RECEIVED, rows in action_log == 1
    base = dict(a5_payload(f"run {RUN}-parallel"), event_id=f"SCD-{time.strftime('%Y%m%d')}-{int(time.time()) % 0xFFFFFF:06X}", company="Aravalli Mobility Pvt Ltd")
    for r in base["rfqs"]:
        r["supplier_email"] = "x@supplier.example"
    with cf.ThreadPoolExecutor(20) as ex:
        outs = list(ex.map(lambda _: requests.post(URL, json=base, headers=H, timeout=30).json().get("status"), range(20)))
    check("20 parallel posts: exactly one RECEIVED", outs.count("RECEIVED") == 1 and outs.count("DUPLICATE") == 19, f"{outs.count('RECEIVED')} received, {outs.count('DUPLICATE')} duplicate")
    check("one action_log row, DELIVERED", sql("SELECT status, count(*) FROM action_log WHERE event_id=%s GROUP BY 1", (base["event_id"],)) == [("DELIVERED", 1)])

    # 3. invalid payloads go to the dead-letter table with a 422
    bad = dict(base, event_id=base["event_id"][:-1] + "F", approval_authority="Head of Supply Chain Management")
    r = requests.post(URL, json=bad, headers=H, timeout=10)
    check("bad approver: HTTP 422 with errors", r.status_code == 422 and "approval_authority" in r.text)
    check("rejected payload recorded in dead_letter", sql("SELECT count(*) FROM dead_letter WHERE source='n8n-validate' AND event_id=%s", (bad["event_id"],))[0][0] >= 1)

    # 4. n8n down: the tool queues the action; restart n8n; replay delivers it
    subprocess.run(["docker", "stop", N8N], capture_output=True)
    n8n_tool.STATE.update(sent=False, queued=False, last_event_id=None)
    out = n8n_tool.trigger_n8n.run(payload_json=json.dumps(a5_payload(f"run {RUN}-down")))
    eid = n8n_tool.STATE["last_event_id"]
    check("n8n down: tool answers 'saved ... retried automatically'", "retried automatically" in out and n8n_tool.STATE["queued"], out[:80])
    check("action_log is QUEUED and dead_letter OPEN", sql("SELECT a.status, d.status FROM action_log a JOIN dead_letter d USING (event_id) WHERE a.event_id=%s", (eid,)) == [("QUEUED", "OPEN")])
    subprocess.run(["docker", "start", N8N], capture_output=True)
    check("n8n back up", wait_hook())
    sql("UPDATE dead_letter SET next_retry_at = now() - interval '1 second' WHERE event_id=%s", (eid,))
    res = dlq.replay()
    check("replay delivered the queued action", res["resolved"] >= 1, str(res))
    check("action_log DELIVERED and dead_letter RESOLVED", sql("SELECT a.status, d.status FROM action_log a JOIN dead_letter d USING (event_id) WHERE a.event_id=%s", (eid,)) == [("DELIVERED", "RESOLVED")])

    # 5. wrong token in the tool: queued (configuration problem), delivered after the token is fixed
    good = os.environ.get("N8N_WEBHOOK_TOKEN")
    os.environ["N8N_WEBHOOK_TOKEN"] = "wrong"
    n8n_tool.STATE.update(sent=False, queued=False, last_event_id=None)
    p2 = a5_payload(f"run {RUN}-token")
    out = n8n_tool.trigger_n8n.run(payload_json=json.dumps(p2))
    eid2 = n8n_tool.STATE["last_event_id"]
    check("wrong token: queued, not rejected", n8n_tool.STATE["queued"] and sql("SELECT status FROM action_log WHERE event_id=%s", (eid2,)) == [("QUEUED",)])
    os.environ["N8N_WEBHOOK_TOKEN"] = good
    sql("UPDATE dead_letter SET next_retry_at = now() - interval '1 second' WHERE event_id=%s", (eid2,))
    check("token fixed: replay delivers", dlq.replay()["resolved"] >= 1 and sql("SELECT status FROM action_log WHERE event_id=%s", (eid2,)) == [("DELIVERED",)])

    # 6. approval lifecycle (SOP-SC-015) through the real tool path, with a controlled clock
    from datetime import timedelta
    from tools import approvals
    prime_rag()
    n8n_tool.STATE.update(sent=False, queued=False, last_event_id=None)
    out = n8n_tool.trigger_n8n.run(payload_json=json.dumps(a5_payload(f"run {RUN}-approval")))
    ev = n8n_tool.STATE["last_event_id"]
    check("tool reports the approval state to the agent", "APPROVAL: PENDING at Chief Financial Officer" in out and "HELD until an approver decides" in out, out[-160:])
    st = approvals.status(ev)
    check("approval opened at the CFO level with documents held", st and st["status"] == "PENDING" and st["required_level"] == "Chief Financial Officer" and st["documents_held"] and st["released_at"] is None)
    payload = sql("SELECT payload FROM action_log WHERE event_id=%s", (ev,))[0][0]
    gaps = {g["part_id"]: g["gap_days"] for g in payload["stockout_gaps"]}
    check("clause 4.7 derived from the ERP: gaps 9/6/6, P-1002 excluded", gaps == {"P-1001": 9, "P-2001": 6, "P-3002": 6}, str(gaps))
    check("clause 4.5 derived: three Production Planning notices", sorted(n["part_id"] for n in payload["production_notifications"]) == ["P-1001", "P-2001", "P-3002"]
          and sql("SELECT count(*) FROM notification_outbox WHERE event_id=%s AND kind='PRODUCTION'", (ev,))[0][0] == 3)
    opened = st["event_id"] and sql("SELECT opened_at FROM approvals WHERE event_id=%s", (ev,))[0][0]
    check("lower authority cannot approve", approvals.decide(ev, "Head of Supply Chain Management", "APPROVE")["outcome"] == "DENIED")
    check("no action before the reminder point", [t for t in approvals.tick(opened + timedelta(minutes=100)) if t["t_event_id"] == ev] == [])
    check("reminder at 50% of the 8 h L2 window", [t["t_action"] for t in approvals.tick(opened + timedelta(minutes=241)) if t["t_event_id"] == ev] == ["REMINDER"])
    check("top level expires: HELD, nothing released", [t["t_action"] for t in approvals.tick(opened + timedelta(minutes=481)) if t["t_event_id"] == ev] == ["HELD"]
          and approvals.status(ev)["status"] == "HELD" and approvals.status(ev)["released_at"] is None)
    d = approvals.decide(ev, "Chief Financial Officer", "APPROVE", "late but legitimate")
    check("a human can still decide after HELD; documents are released once", d["outcome"] == "APPROVED" and d["release"]["released"] and len(d["release"]["documents"]) == 4)
    check("decision is final", approvals.decide(ev, "Chief Financial Officer", "REJECT")["outcome"] == "ALREADY_DECIDED")
    tick_url = URL.replace("supply-chain-rfq", "supply-chain-tick")
    try:
        r = requests.post(tick_url, json={}, headers=H, timeout=60)
        check("n8n tick webhook runs and is token protected", r.status_code == 200 and "notifications_dispatched" in r.json()
              and requests.post(tick_url, json={}, timeout=5).status_code == 403)
    except requests.RequestException as e:
        check("n8n tick webhook runs and is token protected", False, f"{type(e).__name__}")
    r = requests.post(URL.replace("supply-chain-rfq", "supply-chain-approval"), json={"event_id": ev, "approver_level": "Chief Financial Officer", "decision": "APPROVE"}, headers=H, timeout=10)
    check("n8n approval webhook answers 409 for a decided record", r.status_code == 409)

    # 7. the database drops every audit connection: the pool heals itself
    from tools.db import get_engine
    from sqlalchemy import text
    with get_engine("audit").connect() as c:
        c.execute(text("SELECT 1"))
    sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='scm_audit' AND pid <> pg_backend_pid()")
    with get_engine("audit").connect() as c:
        check("pool_pre_ping survives killed connections", c.execute(text("SELECT 1")).scalar() == 1)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
