# Scout — Disruption Scout Agent and Web Search Tool

## 1. How it works (5 lines)
1. `web_search` tool queries live news via Serper (LangChain `GoogleSerperAPIWrapper`, `type="news"`, k=8).
2. The Scout agent (`agents/scout.py`) runs AT LEAST 3 distinct searches: ports, weather/typhoon, geopolitics/strikes.
3. Only events from the last 30 days on watchlist lanes (Taiwan / China / India) are kept, with delay range + worst-case.
4. Output follows the fixed Scout handover format (`## Disruption events` + `## Verdict`) for the Analyst (Person 5).
5. All failures return `SEARCH UNAVAILABLE: ...` — the agent retries with a shorter query, never invents news/URLs.

## 2. Fallback chain
```
Serper news ×3 (waits 2s, 4s, 8s) → DuckDuckGo news (ddgs, no key, max_results=8) → SEARCH UNAVAILABLE message
```
- `[TOOL]` log on Serper success. `[RECOVERY]` log on every retry, on missing `SERPER_API_KEY`, on DDG fallback, and on total failure.
- Output format per result:
```
- <title> | <date> | <source>
  <snippet>
  <url>
```
- Empty query returns `SEARCH UNAVAILABLE` immediately without consuming credits.

## 3. Simulated failure
For the demo / autonomy marks (retry on camera):

```bash
# option A: env var (read dynamically)
SIMULATE_SEARCH_FAILURE=true python -m agents.scout

# option B: programmatic (used by main.py --simulate-search-failure)
from tools.search_tool import enable_simulated_failure
enable_simulated_failure(True)   # next first search, attempt 1 raises TimeoutError, then recovers
enable_simulated_failure(False)  # turn off
```

Expected log:
```
[RECOVERY] Serper attempt 1/3 failed: simulated Serper timeout (demo)
[TOOL] Serper OK (N hits): ...
```
`enable_simulated_failure(True)` resets the call counter so the NEXT search demonstrates the retry.

Without a Serper key the tool skips Serper entirely:
```
[RECOVERY] No SERPER_API_KEY - skipping to DuckDuckGo fallback
```

## 4. Sample outputs (for Persons 5 and 6)

### (a) Canonical typhoon sample — worst-case 21 drives all downstream maths
```text
## Disruption events
1. Location/port: Kaohsiung port | Country: Taiwan
   Type: weather (typhoon) - port closure
   Date: 2026-10-01
   Delay range: 14-21 days | Worst-case delay: 21 days
   Confidence: high
   Sources: https://example.com/typhoon-kaohsiung-closure, https://example.com/taiwan-ports-shut
2. Location/port: Keelung port | Country: Taiwan
   Type: port closure
   Date: 2026-10-01
   Delay range: 14-21 days | Worst-case delay: 21 days
   Confidence: high
   Sources: https://example.com/keelung-port-typhoon
## Verdict
MATERIAL DISRUPTION FOUND
```

### (b) No-disruption sample (Analyst must return empty tables)
```text
## Disruption events
## Verdict
NO MATERIAL DISRUPTION FOUND
```

## 5. Confidence calibration (Part B+ research)
Why: "high/medium/low" without a rule drifts between runs. News reliability signals used here:
source independence (2 independent outlets beat 1 story copied twice), source authority
(port authority / met office / Reuters-AP outrank aggregators / social), and recency
(event date within 7 days beats undated or 30-day-old items, per SOP L1 2-source idea).

Rubric (also embedded in the Scout task wording, never hard-coded to any event):
| Level | Rule |
|---|---|
| `high` | 2+ independent sources incl. 1 official (port authority, met office, Reuters/AP) AND event date within 7 days |
| `medium` | 1 reputable source OR 2 aggregators, date within 30 days, lane match clear |
| `low` | single aggregator/social, or date unclear, or indirect lane impact — still listed but flagged |

Prompt enforcement: "Only use URLs that appear in search results. Every event needs at least 1 real copied URL, prefer official sources."
Test over 5 runs: check the label follows the rule above (e.g. single-aggregator typhoon claim must not be `high`), and that sample (a) typhoon keeps `high` only when 2 sources exist.

## 6. Geofencing to watchlist
Watchlist countries: Taiwan, China, India. Lanes: Kaohsiung, Keelung, Yantian, domestic India road.
Phrasing tested: country-level filter "Ignore events outside the watchlist countries" +
"Keep only events from the last 30 days that plausibly affect Taiwan, China or India lanes in the watchlist."
Why this: listing every port/city bloats the prompt and still misses aliases (e.g. "Taiwan Strait" vs "Kaohsiung").
Country-level geofence cut off-watchlist hits (e.g. US port strikes, Red Sea) in tuning without hard-coding cities.
Experiment variants: (A) embedded mid-task vs (B) repeated as final line "Taiwan/China/India only".
Keep (A); add (B) only if off-watchlist hits persist. Do not hard-code city lists in code.

## 7. No-news behaviour (anti-hallucination)
On a quiet news day the correct output is `NO MATERIAL DISRUPTION FOUND`.
Guards (all active): temperature 0.1, fixed `expected_output` handover format,
"Never invent events or URLs", "Only use URLs that appear in search results",
"Every event needs at least 1 real copied URL", plus tool fallback
`SEARCH UNAVAILABLE: ... report 'no verified signal' - never invent events`.
If all providers fail the agent must retry once with a shorter query, then stop and report
no verified signal — an empty `## Disruption events` section is valid.
Sample (b) above is the no-event shape; Analyst must return empty tables on it.

## 8. Run / test (pinned env — Python 3.11 required)
```bash
# CrewAI 1.15.23 needs Python >=3.10,<3.14. Default python here is 3.14 -> use 3.11:
"C:\Users\cd035\AppData\Local\Programs\Python\Python311\python.exe" -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt  # includes crewai[google-genai] for LLM_MODEL=gemini/...
python -m pytest -q
python -m agents.scout
```
With a focus (Mac/Linux: `SCOUT_FOCUS="Priority focus: typhoon Kaohsiung port" python -m agents.scout`; Windows: set `SCOUT_FOCUS` in `.env` instead).

Local model note: `.env` uses `LLM_MODEL=gemini/gemini-3.8-flash` (free tier). `gemini-2.0-flash`
is retired (API returns 404) — that is why the model was moved. Person 6's merge run can keep
`openai/gpt-4o-mini`; `build_scout_agent(llm)` takes any LLM.

## 9. Tuning log (Step 5 — live evidence 2026-10-01)
Tool queries verified live (Serper OK, ~15 credits used total). Full agent run attempted twice:
it ran **5 distinct searches on its own** (ports congestion, Yantian, typhoon Taiwan,
trade/export-ban/strike, Chennai road) — all Serper OK (0/10/10/10/10 hits), all on-watchlist —
then synthesis was blocked by the **Gemini free-tier 5 RPM limit** (`429 RESOURCE_EXHAUSTED`,
`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`, quotaValue 5, model gemini-3.8-flash).
That is a quota limit, not a code bug: `web_search`, retries, and single LLM calls all pass live.

Remedies for the 5 tuning runs: wait out the `retryDelay` (46–56s) and run at most one agent
run per few minutes; or use Person 6's team `OPENAI_API_KEY` (`openai/gpt-4o-mini`, no 5-RPM cap);
or a Groq key (30 RPM free). Older Gemini flash models (2.0/2.5) are retired for new users (404),
so 3.8-flash is currently the only Gemini option.
Agent-run checklist (fill during quota runs): searches 3+ distinct ✓ (observed 5), relevance
watchlist-only ✓ (observed), real copied URLs, `Worst-case delay: N days` stated, exact handover
format, quiet day → `NO MATERIAL DISRUPTION FOUND`.

Handover: Scout ready; sample outputs above; `enable_simulated_failure()` is in `tools.search_tool`.
