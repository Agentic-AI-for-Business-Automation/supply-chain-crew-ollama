"""Sends the Coordinator's action record to the n8n procurement workflow (validated, idempotent, never lost)."""
import hashlib, json, re, time
from datetime import date, datetime

from crewai.tools import tool
from dotenv import load_dotenv
from pydantic import ValidationError

from schemas import policy_check
from schemas.payload import ActionPayload
from tools import dlq
from tools.dlq import log, scrub as _scrub

load_dotenv()
STATE = {"sent": False, "queued": False, "last_event_id": None}
RETRYABLE_REJECTIONS = {401, 403, 408, 429}   # configuration or load problems, not a bad payload: queue instead of rejecting


def replay_outbox() -> str:
    """Kept for the old CLI flag: drain spill files, then replay the database queue."""
    r = dlq.replay()
    return f"DLQ replay: {r}"


def _load_erp() -> dict:  # indirection so tests can inject a fixed ERP snapshot
    from tools.db import get_engine
    return policy_check.load_erp(get_engine("ro"))


def _retrieved() -> set:
    from tools.rag_tool import RETRIEVED
    return RETRIEVED


def _retrieved_clauses() -> set:
    from tools.rag_tool import RETRIEVED_CLAUSES
    return RETRIEVED_CLAUSES


def _coerce(raw) -> dict:
    """Accept a dict, a JSON string, a fenced block, or JSON surrounded by prose."""
    if isinstance(raw, dict):
        return raw
    s = str(raw or "").strip()
    m = re.search(r"\{.*\}", s, re.S)          # first '{' .. last '}' ignores fences and prose
    return json.loads(m.group(0) if m else s)


def _client_rejection(e: Exception) -> bool:
    code = dlq.status_code(e)
    return code is not None and 400 <= code < 500 and code not in RETRYABLE_REJECTIONS


def _finish_ok(payload: dict, response) -> str:
    try:
        status = response.json().get("status")
    except Exception:
        status = None
    STATE["sent"] = True
    try:
        dlq.set_status(payload["event_id"], "DELIVERED")        # normally n8n has already done it; harmless otherwise
    except Exception as e:
        log("WARN", f"could not mark {payload['event_id']} DELIVERED: {_scrub(e)[:100]}")
    if status == "DUPLICATE":
        log("N8N", f"n8n reports event {payload['event_id']} already processed")
        return "DUPLICATE: n8n had already processed this event; nothing was re-sent. Write the brief now."
    log("N8N", f"Webhook accepted (HTTP {response.status_code}) event {payload['event_id']}")
    return f"SUCCESS. n8n response: {response.text[:1500]}" + dlq.approval_sentence(payload["event_id"])


@tool("Trigger n8n Procurement Workflow")
def trigger_n8n(payload_json: str) -> str:
    """Send the final action record (RFQ / contingency plan / monitor-only) to the procurement
    workflow. Input: ONE JSON object matching the schema in your task. Returns the workflow's
    response, or VALIDATION ERROR details that you must fix before calling again."""
    if STATE["sent"] or STATE["queued"]:
        return (f"ALREADY SENT as {STATE['last_event_id']}. Do NOT call this tool again; "
                "write the executive brief now.")
    try:
        payload = ActionPayload.model_validate(_coerce(payload_json)).model_dump(mode="json")
    except (json.JSONDecodeError, ValidationError, TypeError, AttributeError, ValueError) as e:
        log("RECOVERY", "Payload failed validation -> returned to agent for correction")
        return f"VALIDATION ERROR - fix and call again:\n{str(e)[:1500]}"
    try:
        erp = _load_erp()
        errs = policy_check.check(payload, erp, _retrieved(), _retrieved_clauses())
        if not errs:
            policy_check.derive(payload, erp)           # clauses 4.5 / 4.7: computed from the ERP, never typed by the LLM
    except Exception as e:
        log("RECOVERY", f"Policy check could not reach the ERP: {_scrub(e)[:120]}")
        return f"ERROR: cannot verify the payload against the ERP right now ({_scrub(e)[:200]}). Call this tool again."
    if errs:
        log("RECOVERY", f"Payload broke {len(errs)} policy rule(s) -> returned to agent")
        return "VALIDATION ERROR - fix and call again:\n- " + "\n- ".join(errs)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:6].upper()
    payload.update(event_id=f"SCD-{date.today():%Y%m%d}-{digest}",        # same content -> same id (idempotent)
                   generated_at=datetime.now().isoformat(timespec="seconds"),
                   company="Aravalli Mobility Pvt Ltd", origin="crewai-supply-chain-crew")
    STATE["last_event_id"] = payload["event_id"]
    db_up = True
    try:
        existing = dlq.record_intent(payload)
    except Exception as e:
        db_up = False
        log("RECOVERY", f"audit log unreachable ({_scrub(e)[:100]}); will spill to disk if n8n is also down")
        existing = "PENDING"
    if existing == "DELIVERED":
        STATE["sent"] = True
        return ("DUPLICATE: this exact action was already delivered earlier; nothing was re-sent. Write the brief now."
                + dlq.approval_sentence(payload["event_id"]))
    if existing == "QUEUED":
        STATE["queued"] = True
        return "ALREADY QUEUED: this action is waiting in the retry queue and will be delivered automatically. Write the brief now."
    if existing in ("DEAD", "REJECTED"):
        return f"ERROR: this exact action is already recorded as {existing}. Change the payload or report this."
    last = ""
    for attempt in range(1, 4):
        try:
            return _finish_ok(payload, dlq.post(payload))
        except Exception as e:
            last = _scrub(e)
            log("RECOVERY", f"n8n attempt {attempt}/3 failed: {last}")
            if _client_rejection(e):                       # a 4xx the payload caused: fix it, do not retry or queue
                body = getattr(getattr(e, "response", None), "text", "")[:400]
                if db_up:
                    try: dlq.set_status(payload["event_id"], "REJECTED", last)
                    except Exception: pass
                return f"ERROR: n8n rejected the payload ({last[:200]}): {body}. Fix the listed fields or report this."
            if attempt < 3:
                time.sleep(2 * attempt)
    if db_up:
        try:
            did = dlq.queue_for_retry(payload, last)
            STATE["queued"] = True
            log("RECOVERY", f"n8n unreachable - queued in dead_letter #{did}; retries run with --replay-dlq or the watch loop")
            return (f"n8n unreachable after 3 attempts. The action is saved (event {payload['event_id']}, dead_letter #{did}) "
                    "and will be retried automatically with backoff. Report this.")
        except Exception as e:
            log("RECOVERY", f"retry queue unreachable too: {_scrub(e)[:100]}")
    path = dlq.spill_to_file(payload)
    STATE["queued"] = True
    log("RECOVERY", f"n8n and the database are unreachable - payload spilled to {path}")
    return f"n8n unreachable after 3 attempts. Payload queued locally at {path} for replay. Report this."
