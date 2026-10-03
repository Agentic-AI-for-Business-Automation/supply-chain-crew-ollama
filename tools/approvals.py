"""Operator commands for the approval ladder (SOP-SC-015). Everything goes through the database functions, so the rules
are the same whether a person uses this CLI, the n8n approval webhook or the escalation clock.
  python -m tools.approvals status EVENT_ID
  python -m tools.approvals approve EVENT_ID --as "Chief Financial Officer" [--note "..."]
  python -m tools.approvals reject  EVENT_ID --as "Head of Supply Chain Management" --note "over budget"
  python -m tools.approvals tick                  # run the escalation clock once (n8n runs it every 5 minutes)
  python -m tools.approvals inbox [--to "Chief Financial Officer"]
  python -m tools.approvals routes                                   # who is emailed
  python -m tools.approvals set-route "Chief Financial Officer" cfo@example.com
  python -m tools.approvals clear-route "Chief Financial Officer"
Recipients: the three approvers, "Production Planning", "Procurement" and "SCM Alerts" (n8n failures)."""
import argparse, json
from datetime import datetime, timezone

from sqlalchemy import text

from tools.db import get_engine


def decide(event_id: str, level: str, decision: str, note: str = "") -> dict:
    """decision: APPROVE | REJECT. Returns {'outcome': ..., 'release': {...}|None}."""
    with get_engine("audit").begin() as c:
        outcome = c.execute(text("SELECT approval_decide(:e, :l, :d, :n)"), {"e": event_id, "l": level, "d": decision, "n": note}).scalar()
        release = None
        if outcome == "APPROVED":
            release = c.execute(text("SELECT approval_release(:e)"), {"e": event_id}).scalar()
    return {"outcome": outcome, "release": release}


def tick(now: datetime | None = None) -> list[dict]:
    with get_engine("audit").begin() as c:
        rows = c.execute(text("SELECT t_event_id, t_action, t_from_level, t_to_level, t_deadline FROM approval_tick(COALESCE(:n, now()))"), {"n": now}).mappings()
        return [dict(r) for r in rows]


def status(event_id: str) -> dict | None:
    with get_engine("audit").connect() as c:
        r = c.execute(text("SELECT * FROM v_approval_status WHERE event_id = :e"), {"e": event_id}).mappings().first()
        hist = c.execute(text("SELECT at, kind, actor, note FROM approval_events WHERE event_id = :e ORDER BY id"), {"e": event_id}).mappings().all()
    return dict(r, history=[dict(h) for h in hist]) if r else None


def inbox(recipient: str | None = None, limit: int = 50) -> list[dict]:
    with get_engine("audit").connect() as c:
        rows = c.execute(text("SELECT id, created_at, kind, recipient, subject, body, status FROM notification_outbox "
                              "WHERE (:r IS NULL OR recipient = :r) ORDER BY id DESC LIMIT :n"), {"r": recipient, "n": limit}).mappings()
        return [dict(r) for r in rows]


def routes() -> list[dict]:
    with get_engine("audit").connect() as c:
        return [dict(r) for r in c.execute(text("SELECT recipient, email, updated_at FROM notification_routes ORDER BY recipient")).mappings()]


def set_route(recipient: str, email: str) -> str:
    with get_engine("audit").begin() as c:
        return c.execute(text("SELECT notification_route_set(:r, :e)"), {"r": recipient, "e": email}).scalar()


def clear_route(recipient: str) -> int:
    with get_engine("audit").begin() as c:
        return c.execute(text("SELECT notification_route_clear(:r)"), {"r": recipient}).scalar()


def _show(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Approval ladder operator commands (SOP-SC-015)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("approve", "reject"):
        p = sub.add_parser(name); p.add_argument("event_id"); p.add_argument("--as", dest="level", required=True); p.add_argument("--note", default="")
    sub.add_parser("status").add_argument("event_id")
    sub.add_parser("tick")
    sub.add_parser("inbox").add_argument("--to", default=None)
    sub.add_parser("routes")
    p = sub.add_parser("set-route"); p.add_argument("recipient"); p.add_argument("email")
    sub.add_parser("clear-route").add_argument("recipient")
    a = ap.parse_args(argv)
    if a.cmd in ("approve", "reject"):
        res = decide(a.event_id, a.level, "APPROVE" if a.cmd == "approve" else "REJECT", a.note)
        _show(res)
        return 0 if res["outcome"] in ("APPROVED", "REJECTED") else 1
    if a.cmd == "tick":
        _show(tick()); return 0
    if a.cmd == "status":
        s = status(a.event_id); _show(s or {"error": "no approval record for this event"}); return 0 if s else 1
    if a.cmd == "routes":
        _show(routes()); return 0
    if a.cmd == "set-route":
        out = set_route(a.recipient, a.email); print(out); return 0 if out == "OK" else 1
    if a.cmd == "clear-route":
        print("cleared" if clear_route(a.recipient) else "no such route"); return 0
    _show(inbox(a.to)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
