"""Web search tool for the Disruption Scout: Serper (LangChain) -> DuckDuckGo -> graceful message."""
import os, re, time
from datetime import datetime
from crewai.tools import tool
from dotenv import load_dotenv
from langchain_community.utilities import GoogleSerperAPIWrapper

load_dotenv()
_state = {"calls": 0, "simulate": False}
_SEEN_URLS: set[str] = set()   # every URL a search returned: the Scout may cite only these


def seen_urls() -> set[str]:
    return set(_SEEN_URLS)


def reset_seen_urls() -> None:
    _SEEN_URLS.clear()


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


def _simulate_on() -> bool:
    # Read env dynamically (main.py may set it after import) + explicit toggle for tests/demo
    return _state["simulate"] or os.getenv("SIMULATE_SEARCH_FAILURE", "false").strip().lower() in ("true", "1", "yes")


def enable_simulated_failure(on: bool = True) -> None:
    """Called by main.py when run with --simulate-search-failure."""
    _state["simulate"] = on
    _state["calls"] = 0  # reset so the NEXT search demonstrates the retry on camera


_INJECTION = re.compile(r"(?i)(ignore (all |any )?(previous|prior|above)|system prompt|you are now|"
                        r"(call|use|run) the [\w ]+ tool|send (an? )?rfq|disregard)")


def _retryable(e: Exception) -> bool:
    """Auth/quota/bad-request errors will not fix themselves; 429 and 5xx/timeouts might."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return code is None or code == 429 or code >= 500


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
        snippet = _INJECTION.sub("[removed]", str(raw.get("snippet") or raw.get("body") or "").strip())[:400]
        title = _INJECTION.sub("[removed]", title)[:200]
        url = str(raw.get("link") or raw.get("url") or "").strip()
        if url:
            _SEEN_URLS.add(url)
        lines.append(f"- {title} | {date} | {source}\n  {snippet}\n  {url}")
    if not lines:
        return "NO RESULTS for this query."
    return "<untrusted_search_results>\n" + "\n".join(lines) + "\n</untrusted_search_results>"


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
                if not _retryable(e):
                    break
                if attempt < 3:
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
        except (TypeError, AttributeError):
            # very old clients without context-manager support
            res = list(DDGS().news(q, max_results=8)) or []
        log("RECOVERY", f"Fell back to DuckDuckGo ({len(res)} hits): {q}")
        return _fmt(res)
    except Exception as e:
        log("RECOVERY", f"DuckDuckGo failed: {e}")
    return ("SEARCH UNAVAILABLE: all providers failed. Try ONE shorter, different query. "
            "If still unavailable, report 'no verified signal' - never invent events.")
