"""Web search tool for the Disruption Scout: Serper (LangChain) -> DuckDuckGo -> graceful message."""
import os, time
from datetime import datetime
from crewai.tools import tool
from dotenv import load_dotenv
from langchain_community.utilities import GoogleSerperAPIWrapper

load_dotenv()
_state = {"calls": 0, "simulate": False}


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


def _simulate_on() -> bool:
    # Read env dynamically (main.py may set it after import) + explicit toggle for tests/demo
    return _state["simulate"] or os.getenv("SIMULATE_SEARCH_FAILURE", "false").strip().lower() in ("true", "1", "yes")


def enable_simulated_failure(on: bool = True) -> None:
    """Called by main.py when run with --simulate-search-failure."""
    _state["simulate"] = on
    _state["calls"] = 0  # reset so the NEXT search demonstrates the retry on camera


def _fmt(items: list) -> str:
    if not isinstance(items, list):
        return "NO RESULTS for this query."
    lines = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "Untitled").strip()
        date = str(raw.get("date") or "").strip()
        source = str(raw.get("source") or "").strip()
        snippet = str(raw.get("snippet") or raw.get("body") or "").strip()
        url = str(raw.get("link") or raw.get("url") or "").strip()
        lines.append(f"- {title} | {date} | {source}\n  {snippet}\n  {url}")
    return "\n".join(lines) or "NO RESULTS for this query."


@tool("Web Search")
def web_search(query: str) -> str:
    """Search live news for supply-chain disruption signals: port congestion or closures, typhoons
    and extreme weather, strikes, sanctions, export bans, geopolitical tension.
    Input: ONE focused query, e.g. 'Kaohsiung port typhoon closure'.
    Returns headline | date | source, a snippet and the URL for each result."""
    q = (query or "").strip()[:300]
    if not q:
        return "SEARCH UNAVAILABLE: empty query. Retry with e.g. 'Kaohsiung port closure'."
    _state["calls"] += 1
    if os.getenv("SERPER_API_KEY"):
        serper = GoogleSerperAPIWrapper(type="news", k=8)
        for attempt in range(1, 4):
            try:
                if _simulate_on() and _state["calls"] == 1 and attempt == 1:
                    raise TimeoutError("simulated Serper timeout (demo)")
                news = (serper.results(q) or {}).get("news", []) or []
                log("TOOL", f"Serper OK ({len(news)} hits): {q}")
                return _fmt(news)
            except Exception as e:
                log("RECOVERY", f"Serper attempt {attempt}/3 failed: {e}")
                time.sleep(2 ** attempt)
    else:
        log("RECOVERY", "No SERPER_API_KEY - skipping to DuckDuckGo fallback")
    try:
        try:
            from ddgs import DDGS
        except ImportError:  # older images still ship duckduckgo_search
            from duckduckgo_search import DDGS
        try:
            with DDGS() as ddgs:
                res = list(ddgs.news(q, max_results=8)) or []
        except TypeError:
            # very old clients without context-manager support
            res = list(DDGS().news(q, max_results=8)) or []
        log("RECOVERY", f"Fell back to DuckDuckGo ({len(res)} hits): {q}")
        return _fmt(res)
    except Exception as e:
        log("RECOVERY", f"DuckDuckGo failed: {e}")
    return ("SEARCH UNAVAILABLE: all providers failed. Try ONE shorter, different query. "
            "If still unavailable, report 'no verified signal' - never invent events.")
