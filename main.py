"""Supply Chain Disruption Monitoring & Mitigation Crew - entry point.
Scout (web) -> Inventory Impact Analyst (ERP) -> Operations Coordinator (RAG + n8n)."""
import argparse, json, os, time
from datetime import date
from dotenv import load_dotenv
load_dotenv()
from crewai import Crew, LLM, Process
from agents.scout import build_scout_agent, build_scout_task
from agents.analyst import build_analyst_agent, build_analyst_task
from agents.coordinator import build_coordinator_agent, build_coordinator_task
from schemas.payload import ActionPayload
from tools.sql_tools import get_db, get_watchlist
from tools.preflight import PreflightError, run_gate
from tools.rag_tool import build_index
from tools.search_tool import enable_simulated_failure, reset_seen_urls
from tools.n8n_tool import STATE, log

LLM_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "120"))   # per LLM call
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "1"))          # litellm backoff on 429/5xx
MAX_RPM = int(os.getenv("MAX_RPM", "20"))                 # crew-wide request cap
AGENT_MAX_SECONDS = int(os.getenv("AGENT_MAX_SECONDS", "300"))


def build_llm() -> LLM:
    """Build the application LLM using local Ollama only."""

    model = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    return LLM(
        model=model,
        provider="ollama",
        base_url=base_url,
        api_key="ollama",
        temperature=0.1,
        timeout=LLM_TIMEOUT_S,
        max_retries=LLM_RETRIES,
    )


def run_once(focus: str, watch: bool = False, scout_report: str | None = None) -> None:
    STATE.update(sent=False, queued=False, last_event_id=None)
    reset_seen_urls()
    from tools.rag_tool import reset_rag_session
    reset_rag_session()
    if watch:                                        # self-healing: deliver anything a previous cycle could not
        from tools.dlq import replay
        replay()
    log("BOOT", "Connecting to ERP and building the rulebook index...")
    try:
        try:
            from tools.migrate import migrate
            migrate()                                # idempotent; brings an older database up to the current schema
        except Exception as e:                       # e.g. only read-only credentials are configured
            log("WARN", f"schema migration skipped ({type(e).__name__}); the integrity gate below verifies the schema")
        run_gate(get_db(), log)
        from tools.kb_lint import lint, load_records
        kb_problems = lint(load_records())
        if kb_problems:
            raise PreflightError("knowledge base lint failed: " + "; ".join(kb_problems[:3]))
        watchlist = get_watchlist()
        if not watchlist.strip():
            raise PreflightError("the ERP returned an empty supplier watchlist")
        if build_index() <= 0:
            raise PreflightError("the knowledge base index is empty")
    except FileNotFoundError as e:
        raise PreflightError(f"knowledge base missing: {e}")
    except PreflightError:
        raise
    except Exception as e:
        raise PreflightError(f"cannot reach the ERP or knowledge base ({type(e).__name__}: {str(e)[:150]})")
    log("BOOT", f"Watchlist from ERP: {watchlist}")
    llm = build_llm()
    scout = build_scout_agent(llm);             scout_task = build_scout_task(scout)
    if scout_report:                             # replay: a saved report stands in for the live web search
        from crewai.tasks.task_output import TaskOutput
        from schemas.ingress import _URL, check_scout_report
        _, problems = check_scout_report(scout_report, set(_URL.findall(scout_report)))
        if problems:
            raise PreflightError("the replayed Scout report is invalid: " + "; ".join(problems[:3]))
        scout_task.output = TaskOutput(description="replayed Scout report", raw=scout_report, agent=scout.role)
        log("BOOT", "Scout step replaced by a saved report (replay mode); Analyst and Coordinator run for real")
    analyst = build_analyst_agent(llm);         analyst_task = build_analyst_task(analyst, [scout_task])
    coord = build_coordinator_agent(llm);       coord_task = build_coordinator_task(coord, [scout_task, analyst_task])
    for a in (scout, analyst, coord):
        a.max_execution_time = AGENT_MAX_SECONDS
    # memory needs OpenAI embeddings (sends crew text to OpenAI) and carries old events into later watch cycles
    use_memory = False
    agents, tasks = ([analyst, coord], [analyst_task, coord_task]) if scout_report else ([scout, analyst, coord], [scout_task, analyst_task, coord_task])
    crew = Crew(agents=agents, tasks=tasks,
                process=Process.sequential, memory=use_memory, max_rpm=MAX_RPM, verbose=True)
    result = crew.kickoff(inputs={
        "today": date.today().isoformat(),
        "watchlist": watchlist,
        "focus": f"Priority focus: {focus}" if focus else "",
        "payload_schema": json.dumps(ActionPayload.model_json_schema())})
    from schemas.ingress import parse_scout_report
    material = False
    try:
        material = parse_scout_report(scout_task.output.raw).material
    except Exception:
        pass
    if material and not (STATE["sent"] or STATE["queued"]):
        banner = ("> **WARNING - NOT EXECUTED.** The Coordinator did not trigger the procurement workflow for a material disruption. "
                  "Nothing was sent to n8n and no approval was opened. Any statement below claiming otherwise (event id, n8n response, "
                  "RFQs raised) was invented by the model and must be ignored.\n\n")
        try:
            with open("reports/action_brief.md", "r+", encoding="utf-8") as f:
                body = f.read(); f.seek(0); f.write(banner + body)
        except OSError:
            pass
        raise RuntimeError("Coordinator finished without triggering n8n for a material disruption (brief marked NOT EXECUTED)")
    if STATE["queued"]:
        log("WARN", "n8n was unreachable: delivery is queued in the dead-letter queue - it retries automatically with backoff, or run `python main.py --replay-dlq`")
    elif not STATE["sent"]:
        log("WARN", "Coordinator finished without triggering n8n - check reports/action_brief.md")
    print("\n" + "=" * 80 + "\nFINAL ACTION BRIEF\n" + "=" * 80 + f"\n{result.raw}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Supply chain disruption crew")
    ap.add_argument("--focus", default="", help="e.g. 'typhoon Kaohsiung Keelung port closure'")
    ap.add_argument("--watch", type=int, default=0, help="repeat every N minutes (continuous monitoring)")
    ap.add_argument("--simulate-search-failure", action="store_true", help="demo error recovery")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate wiring (ERP watchlist + RAG index + payload schema) without calling the LLM")
    ap.add_argument("--scout-report", metavar="FILE", help="replay a saved Scout report (see samples/scout_typhoon.md) instead of searching the web")
    ap.add_argument("--no-memory", action="store_true", help="compatibility flag; CrewAI memory is disabled in Ollama-only mode")
    ap.add_argument("--replay-dlq", "--replay-outbox", dest="replay_dlq", action="store_true",
                    help="retry queued deliveries from the dead-letter queue (and legacy outbox/ files), then exit")
    args = ap.parse_args()
    if args.replay_dlq:
        from tools.dlq import replay
        print("DLQ replay:", replay())
        raise SystemExit(0)
    if args.dry_run:  # cheap pre-flight: no LLM cost
        from tools.sql_tools import get_watchlist
        from tools.rag_tool import build_index
        blocking, warnings = __import__("tools.preflight", fromlist=["x"]).erp_integrity_gate(get_db())
        print("integrity blocking:", blocking or "none", "| warnings:", warnings or "none")
        print("watchlist:", get_watchlist())
        print("rag chunks:", build_index())
        print("payload schema OK:", bool(ActionPayload.model_json_schema()))
        raise SystemExit(0)
    if args.simulate_search_failure:
        enable_simulated_failure(True)
    if args.no_memory:
        os.environ["ENABLE_MEMORY"] = "false"
    attempts_allowed = 1 if args.watch else max(1, int(os.getenv("CREW_ATTEMPTS", "2")))   # the watch loop already retries next cycle
    report_text = open(args.scout_report, encoding="utf-8").read() if args.scout_report else None

    def cycle() -> None:
        for attempt in range(1, attempts_allowed + 1):
            try:
                run_once(args.focus, watch=bool(args.watch), scout_report=report_text)
                return
            except PreflightError:
                raise
            except Exception as e:                  # a model that loops or returns malformed output is transient: try once more
                if attempt == attempts_allowed:
                    raise
                log("WARN", f"crew attempt {attempt}/{attempts_allowed} failed ({type(e).__name__}: {str(e)[:120]}); retrying")

    while True:
        try:
            cycle()
        except PreflightError as e:                 # unsafe inputs: never start the agents
            log("WARN", str(e))
            if not args.watch:
                raise SystemExit(2)
        except Exception as e:                      # one failed cycle must not end continuous monitoring
            if not args.watch:
                raise
            log("WARN", f"cycle failed: {type(e).__name__}: {str(e)[:200]}")
        if not args.watch:
            break
        log("WATCH", f"Sleeping {args.watch} min before next scan")
        time.sleep(args.watch * 60)
