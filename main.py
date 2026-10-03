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
from tools.sql_tools import get_watchlist
from tools.rag_tool import build_index
from tools.search_tool import enable_simulated_failure
from tools.n8n_tool import STATE, log


def build_llm() -> LLM:
    model = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    if model.startswith("ollama/"):
        return LLM(model=model, base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"), temperature=0.1)
    return LLM(model=model, temperature=0.1)


def run_once(focus: str) -> None:
    STATE.update(sent=False, last_event_id=None)
    log("BOOT", "Connecting to ERP and building the rulebook index...")
    watchlist = get_watchlist()
    build_index()
    log("BOOT", f"Watchlist from ERP: {watchlist}")
    llm = build_llm()
    scout = build_scout_agent(llm);             scout_task = build_scout_task(scout)
    analyst = build_analyst_agent(llm);         analyst_task = build_analyst_task(analyst, [scout_task])
    coord = build_coordinator_agent(llm);       coord_task = build_coordinator_task(coord, [scout_task, analyst_task])
    use_memory = os.getenv("ENABLE_MEMORY", "true").lower() == "true" and bool(os.getenv("OPENAI_API_KEY"))
    crew = Crew(agents=[scout, analyst, coord], tasks=[scout_task, analyst_task, coord_task],
                process=Process.sequential, memory=use_memory, verbose=True)
    result = crew.kickoff(inputs={
        "today": date.today().isoformat(),
        "watchlist": watchlist,
        "focus": f"Priority focus: {focus}" if focus else "",
        "payload_schema": json.dumps(ActionPayload.model_json_schema())})
    if not STATE["sent"]:
        log("WARN", "Coordinator finished without triggering n8n - check reports/action_brief.md")
    print("\n" + "=" * 80 + "\nFINAL ACTION BRIEF\n" + "=" * 80 + f"\n{result.raw}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Supply chain disruption crew")
    ap.add_argument("--focus", default="", help="e.g. 'typhoon Kaohsiung Keelung port closure'")
    ap.add_argument("--watch", type=int, default=0, help="repeat every N minutes (continuous monitoring)")
    ap.add_argument("--simulate-search-failure", action="store_true", help="demo error recovery")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate wiring (ERP watchlist + RAG index + payload schema) without calling the LLM")
    ap.add_argument("--no-memory", action="store_true", help="disable CrewAI memory even if OPENAI_API_KEY is set")
    ap.add_argument("--replay-outbox", action="store_true", help="re-send queued outbox/*.json to n8n and exit")
    args = ap.parse_args()
    if args.replay_outbox:
        from tools.n8n_tool import replay_outbox
        print(replay_outbox())
        raise SystemExit(0)
    if args.dry_run:  # cheap pre-flight: no LLM cost
        from tools.sql_tools import get_watchlist
        from tools.rag_tool import build_index
        print("watchlist:", get_watchlist())
        print("rag chunks:", build_index())
        print("payload schema OK:", bool(ActionPayload.model_json_schema()))
        raise SystemExit(0)
    if args.simulate_search_failure:
        enable_simulated_failure(True)
    if args.no_memory:
        os.environ["ENABLE_MEMORY"] = "false"
    while True:
        run_once(args.focus)
        if not args.watch:
            break
        log("WATCH", f"Sleeping {args.watch} min before next scan")
        time.sleep(args.watch * 60)
