"""Sends the Coordinator's action record to the n8n procurement workflow."""
import json, os, re, time, uuid
from datetime import date, datetime
import requests
from crewai.tools import tool
from dotenv import load_dotenv
from pydantic import ValidationError
from schemas.payload import ActionPayload

load_dotenv()
def _n8n_url() -> str:  # read at call time so .env edits / tests via monkeypatch work
    return os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/supply-chain-rfq")
OUTBOX = "outbox"
STATE = {"sent": False, "last_event_id": None}


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


def replay_outbox() -> str:
    """Re-send any queued payloads (python -c "from tools.n8n_tool import replay_outbox; print(replay_outbox())")."""
    import glob as _glob
    done, failed = 0, []
    for path in sorted(_glob.glob(os.path.join(OUTBOX, "*.json"))):
        try:
            with open(path) as f: payload = json.load(f)
            r = requests.post(_n8n_url(), json=payload, timeout=20)
            r.raise_for_status()
            os.remove(path)
            done += 1
        except Exception as e:
            failed.append(f"{path}: {e}")
    return f"Replayed {done} payload(s). Failures: {failed or 'none'}"


@tool("Trigger n8n Procurement Workflow")
def trigger_n8n(payload_json: str) -> str:
    """Send the final action record (RFQ / contingency plan / monitor-only) to the procurement
    workflow. Input: ONE JSON object matching the schema in your task. Returns the workflow's
    response, or VALIDATION ERROR details that you must fix before calling again."""
    try:
        cleaned = re.sub(r"^\s*```(?:json|JSON)?\s*", "", (payload_json or "").strip())
        cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
        payload = ActionPayload.model_validate(json.loads(cleaned)).model_dump()
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        log("RECOVERY", "Payload failed validation -> returned to agent for correction")
        return f"VALIDATION ERROR - fix and call again:\n{str(e)[:1500]}"
    payload.update(event_id=f"SCD-{date.today():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
                   generated_at=datetime.now().isoformat(timespec="seconds"),
                   company="Aravalli Mobility Pvt Ltd", origin="crewai-supply-chain-crew")
    STATE["last_event_id"] = payload["event_id"]
    for attempt in range(1, 4):
        try:
            r = requests.post(_n8n_url(), json=payload, timeout=20)
            r.raise_for_status()
            STATE["sent"] = True
            log("N8N", f"Webhook accepted (HTTP {r.status_code}) event {payload['event_id']}")
            return f"SUCCESS. n8n response: {r.text[:1500]}"
        except Exception as e:
            log("RECOVERY", f"n8n attempt {attempt}/3 failed: {e}")
            time.sleep(2 * attempt)
    os.makedirs(OUTBOX, exist_ok=True)
    path = os.path.join(OUTBOX, f"{payload['event_id']}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    STATE["sent"] = True
    log("RECOVERY", f"n8n unreachable - payload queued at {path}")
    return f"n8n unreachable after 3 attempts. Payload queued locally at {path} for replay. Report this."
