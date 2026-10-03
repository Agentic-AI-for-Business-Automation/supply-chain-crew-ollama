"""Delivery state machine and dead-letter queue for the n8n boundary.
action_log lifecycle:  PENDING -> DELIVERED | QUEUED | REJECTED ;  QUEUED -> DELIVERED | DEAD
dead_letter lifecycle: OPEN -> RETRYING -> RESOLVED | OPEN (exponential backoff) | DEAD (6 attempts or a permanent 4xx)"""
import glob, json, os, re
from datetime import datetime

import requests
from sqlalchemy import text

from tools.db import get_engine

OUTBOX = "outbox"                                    # last-resort spill file, used only while the database itself is down
PERMANENT = {400, 404, 409, 410, 413, 415, 422}      # re-sending the same payload can never succeed


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


def scrub(msg) -> str:
    return re.sub(r"://[^/@\s]+@", "://***@", str(msg))


def webhook_url() -> str:
    return os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/supply-chain-rfq")


def post(payload: dict) -> requests.Response:
    headers = {"Idempotency-Key": payload["event_id"]}
    token = os.getenv("N8N_WEBHOOK_TOKEN")
    if token:
        headers["X-SCM-Token"] = token
    r = requests.post(webhook_url(), json=payload, timeout=20, headers=headers)
    r.raise_for_status()
    return r


def status_code(e: Exception) -> int | None:
    resp = getattr(e, "response", None)
    return resp.status_code if isinstance(e, requests.HTTPError) and resp is not None else None


def is_permanent(e: Exception) -> bool:
    return status_code(e) in PERMANENT


# ------------------------------------------------------------------ action_log
def record_intent(payload: dict) -> str:
    """Insert the action record as PENDING (SOP 6.3) and return its current status; an existing row is never overwritten."""
    total = sum(r["est_value_inr"] for r in payload["rfqs"])
    with get_engine("audit").begin() as c:
        c.execute(text("INSERT INTO action_log (event_id, action_type, severity, total_value_inr, approver, payload) "
                       "VALUES (:e, :a, :s, :t, :ap, CAST(:p AS jsonb)) ON CONFLICT (event_id) DO NOTHING"),
                  {"e": payload["event_id"], "a": payload["action_type"], "s": payload["severity"], "t": total,
                   "ap": payload["approval_authority"], "p": json.dumps(payload)})
        return c.execute(text("SELECT status FROM action_log WHERE event_id = :e"), {"e": payload["event_id"]}).scalar()


def set_status(event_id: str, new: str, error: str | None = None, only_from: tuple[str, ...] = ("PENDING", "QUEUED")) -> None:
    with get_engine("audit").begin() as c:
        c.execute(text("UPDATE action_log SET status = :n, last_error = :err, attempts = attempts + 1 "
                       "WHERE event_id = :e AND status = ANY(:f)"),
                  {"n": new, "err": (error or None) and error[:500], "e": event_id, "f": list(only_from)})


def queue_for_retry(payload: dict, error: str) -> int | None:
    """Delivery failed: mark QUEUED and open a retryable dead_letter row (first retry in one minute)."""
    with get_engine("audit").begin() as c:
        c.execute(text("UPDATE action_log SET status = 'QUEUED', last_error = :err, attempts = attempts + 1 "
                       "WHERE event_id = :e AND status = 'PENDING'"), {"err": error[:500], "e": payload["event_id"]})
        return c.execute(text("INSERT INTO dead_letter (source, event_id, payload, retryable, last_error, next_retry_at, errors) "
                              "VALUES ('tool-delivery', :e, CAST(:p AS jsonb), TRUE, :err, now() + interval '1 minute', CAST(:j AS jsonb)) RETURNING id"),
                         {"e": payload["event_id"], "p": json.dumps(payload), "err": error[:500], "j": json.dumps([error[:300]])}).scalar()


def spill_to_file(payload: dict) -> str:
    """Only when the database cannot be reached: keep the payload on disk, atomically."""
    os.makedirs(OUTBOX, exist_ok=True)
    path = os.path.join(OUTBOX, f"{payload['event_id']}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ replay (self-healing)
def adopt_outbox_files() -> int:
    """Move spill files into the database queue once the database is reachable again."""
    moved = 0
    for path in sorted(glob.glob(os.path.join(OUTBOX, "*.json"))):
        try:
            with open(path) as f:
                payload = json.load(f)
            record_intent(payload)
            queue_for_retry(payload, "adopted from outbox file")
            os.remove(path)
            moved += 1
        except Exception as e:
            log("WARN", f"could not adopt {path}: {scrub(e)[:120]}")
    return moved


def replay(batch: int = 10) -> dict:
    """Claim due rows (SKIP LOCKED, safe with several workers), re-post them, and record the outcome."""
    result = {"adopted": 0, "resolved": 0, "retry": 0, "dead": 0}
    try:
        result["adopted"] = adopt_outbox_files()
        with get_engine("audit").begin() as c:
            rows = [dict(r) for r in c.execute(text("SELECT id, event_id, payload FROM dlq_claim(:b)"), {"b": batch}).mappings()]
    except Exception as e:
        log("WARN", f"DLQ replay skipped, database unreachable: {scrub(e)[:120]}")
        return result
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        try:
            post(payload)
            outcome = "RESOLVED"
            set_status(row["event_id"], "DELIVERED", only_from=("QUEUED", "PENDING"))
            err = None
        except Exception as e:
            err = scrub(e)[:300]
            outcome = "DEAD" if is_permanent(e) else None
        with get_engine("audit").begin() as c:
            if outcome == "DEAD":
                c.execute(text("UPDATE dead_letter SET status='DEAD', last_error=:e WHERE id=:i"), {"e": err, "i": row["id"]})
                c.execute(text("UPDATE action_log SET status='DEAD', last_error=:e WHERE event_id=:v AND status='QUEUED'"), {"e": err, "v": row["event_id"]})
            else:
                outcome = c.execute(text("SELECT dlq_release(:i, :ok, :e)"), {"i": row["id"], "ok": err is None, "e": err}).scalar()
        key = {"RESOLVED": "resolved", "OPEN": "retry", "DEAD": "dead"}.get(outcome, "retry")
        result[key] += 1
        log("N8N" if key == "resolved" else "RECOVERY", f"DLQ {row['event_id']}: {outcome}" + (f" ({err})" if err else ""))
    return result


# ------------------------------------------------------------------ approval status (SOP-SC-015), read back for the agent
def approval_info(event_id: str) -> dict | None:
    try:
        with get_engine("audit").connect() as c:
            r = c.execute(text("SELECT status, required_level, current_level, deadline_at, escalations FROM v_approval_status WHERE event_id = :e"),
                          {"e": event_id}).mappings().first()
        return dict(r) if r else None
    except Exception:
        return None


def approval_sentence(event_id: str) -> str:
    info = approval_info(event_id)
    if not info:
        return ""
    if info["status"] in ("PENDING", "ESCALATED"):
        return (f" APPROVAL: {info['status']} at {info['current_level']} until {info['deadline_at']:%Y-%m-%d %H:%M} UTC "
                "(escalates one level per missed window, then HELD; SOP-SC-015). The RFQ documents are HELD until an approver decides; "
                "do NOT say they were sent to suppliers.")
    return f" APPROVAL: {info['status']}."
