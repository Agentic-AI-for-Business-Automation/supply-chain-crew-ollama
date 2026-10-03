# scm-scout-agent — Disruption Scout Agent + Web Search Tool

Person 4 module: the "news reporter". Searches the live web for storms, port closures,
strikes and trade problems affecting our suppliers, and keeps working even when the
search service fails.

> Team note: Person 6 owns `README.md` in the final merged repo. This file is for
> this repo's GitHub page only — delete it before handing off to Person 6.

## Layout (owned paths)

```
tools/search_tool.py    # web_search CrewAI tool (Serper -> DuckDuckGo -> graceful message)
agents/scout.py         # build_scout_agent(llm), build_scout_task(agent), stand-alone test run
tests/test_search_tool.py  # pytest suite (mocked, uses 0 search credits)
docs/scout.md           # how it works, fallback chain, samples, tuning log
```

No `__init__.py` files (packages work without them; avoids merge conflicts).

## Setup (Python 3.11 required)

`crewai==1.15.23` needs Python `>=3.10,<3.14` — the default Python 3.14 on this
machine cannot install it. Use 3.11:

```bash
"C:\Users\cd035\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt  # includes crewai[google-genai] for Gemini models
```

## Environment (local `.env`, never committed)

```
SERPER_API_KEY=...        # serper.dev (free 2,500 queries, no card)
GOOGLE_API_KEY=...        # aistudio.google.com/apikey (Gemini free tier)
LLM_MODEL=gemini/gemini-3.8-flash
SIMULATE_SEARCH_FAILURE=false
```

`requirements.txt` and this README are local-only (Person 6 owns both on merge).

## Usage

```bash
python -m pytest -q        # 10 tests, mocked — spends 0 credits
python -m agents.scout     # live run (~3-5 Serper searches + LLM calls)
```

With a focus (Mac/Linux):

```bash
SCOUT_FOCUS="Priority focus: typhoon Kaohsiung port" python -m agents.scout
```

On Windows set `SCOUT_FOCUS` in `.env` instead. Demo the retry on camera:

```bash
SIMULATE_SEARCH_FAILURE=true python -m agents.scout
# expect: [RECOVERY] Serper attempt 1/3 failed: simulated... then [TOOL] Serper OK
```

Or programmatically (used by `main.py --simulate-search-failure`):

```python
from tools.search_tool import enable_simulated_failure
enable_simulated_failure(True)   # next first search fails once, then recovers
```

## How it works

1. `web_search` queries Serper news (`type="news"`, k=8) with 3 attempts (2s/4s/8s waits).
2. On failure or missing key it falls back to DuckDuckGo (`ddgs`, no key), else returns
   `SEARCH UNAVAILABLE: ...` — the agent retries once with a shorter query, never invents news.
3. The Scout agent runs AT LEAST 3 distinct searches (ports, weather/typhoon,
   geopolitics-strikes), keeps only last-30-days events on Taiwan/China/India lanes,
   and outputs the fixed handover format (`## Disruption events` + `## Verdict`) that
   Person 5's Analyst reads. Full details + confidence rubric in `docs/scout.md`.

## Known free-tier limits

- Serper free: 2,500 queries one-time (6-month expiry). Tests are mocked; a live agent
  run uses ~5. Check usage on the serper.dev dashboard on 403s.
- Gemini `3.8-flash` free tier: **5 requests/min** — a full agent run can hit `429`.
  Space runs minutes apart, or use the team OpenAI key / Groq key for tuning.
  Older flash models (2.0/2.5) are retired (404).

## Handover

Sample outputs (canonical 21-day typhoon + no-disruption) are in `docs/scout.md` for
Persons 5 and 6. Tag release with `git tag v1.0` (Part A9) after the 5 tuning runs pass.
