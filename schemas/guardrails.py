"""CrewAI task guardrails: validate each agent's output deterministically before the next agent sees it.
A failing guardrail returns (False, feedback); CrewAI re-runs the agent with that feedback (guardrail_max_retries)."""
from typing import Any, Tuple

from sqlalchemy import text

from schemas.ingress import check_analyst_report, check_scout_report, parse_scout_report

GUARDRAIL_RETRIES = 2


def _log(msg: str) -> None:
    from tools.n8n_tool import log
    log("RECOVERY", msg)


def scout_guardrail(output) -> Tuple[bool, Any]:
    from tools.search_tool import seen_urls
    rep, errs = check_scout_report(output.raw, seen_urls())
    if errs:
        _log(f"Scout output rejected ({len(errs)} issue(s)): {errs[0][:100]}")
        return False, "Fix these problems and answer again in the exact handover format:\n- " + "\n- ".join(errs)
    return True, output.raw


def load_exposure_erp(engine) -> dict:
    """Per-part facts the Analyst numbers are recomputed from."""
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT i.part_id, i.on_hand_qty::numeric / i.daily_consumption AS cover, i.daily_consumption AS daily, "
            "s.supplier_id AS primary, s.country, s.export_port AS port "
            "FROM inventory i JOIN supplier_parts sp ON sp.part_id = i.part_id AND sp.sourcing_role = 'PRIMARY' "
            "JOIN suppliers s ON s.supplier_id = sp.supplier_id")).mappings().all()
    return {"parts": {r["part_id"]: {"cover": float(r["cover"]), "daily": int(r["daily"]), "primary": r["primary"],
                                     "country": r["country"], "port": r["port"]} for r in rows}}


def make_analyst_guardrail(scout_task, engine_factory=None):
    """Closure over the Scout task so the Analyst is checked against the Scout's actual worst-case delay."""
    def guardrail(output) -> Tuple[bool, Any]:
        try:
            if engine_factory is None:
                from tools.db import get_engine
                engine = get_engine("ro")
            else:
                engine = engine_factory()
            scout_raw = getattr(getattr(scout_task, "output", None), "raw", "") or ""
            scout = parse_scout_report(scout_raw) if scout_raw else None
            errs = check_analyst_report(output.raw, scout, load_exposure_erp(engine))
        except Exception as e:                      # cannot verify = do not pass it on
            _log(f"Analyst output could not be verified: {type(e).__name__}")
            return False, f"The ERP could not be reached to verify your numbers ({type(e).__name__}); run your queries again and re-submit."
        if errs:
            _log(f"Analyst output rejected ({len(errs)} issue(s)): {errs[0][:100]}")
            return False, "Your numbers do not match the ERP. Fix these and answer again in the exact handover format:\n- " + "\n- ".join(errs)
        return True, output.raw
    return guardrail
