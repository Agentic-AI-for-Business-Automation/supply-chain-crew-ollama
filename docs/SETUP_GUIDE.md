# Setup guide: run the Supply Chain Crew on a new computer

Time needed: about 15 minutes, plus downloads. You do not need to understand the code to follow this.

**What you get:** three AI agents that watch the news for supplier disruptions, check your inventory database, apply the company rules, and send a purchase request to an approval workflow. Orders are held until a person approves; if nobody answers, the request escalates and finally stops (it is never approved automatically).

---

## 1. Install the prerequisites (once)

| You need | Version | Check it works | Get it |
|---|---|---|---|
| Docker with Compose v2 | any recent | `docker compose version` | docker.com/products/docker-desktop (Linux: your package manager, then `sudo systemctl start docker`) |
| Python | 3.10, 3.11, 3.12 or 3.13 | `python3 --version` | python.org. **Not 3.14**: the AI library does not support it. |
| Python venv (Ubuntu/Debian only) | | `python3 -m venv --help` | `sudo apt install python3-venv` |
| Internet | | | needed on the first run (downloads about 1 GB: Docker images, Python packages, a small search model) |
| One AI model | | | OpenAI key **or** a local Ollama model (section 3) |

**Windows:** use WSL2 (Ubuntu) and run every command below inside the Ubuntu terminal. In Docker Desktop turn on *Settings → Resources → WSL integration*. Do not use PowerShell for these commands.

## 2. Get the project

Copy or unzip the project folder `supply-chain-crew` onto the new computer (or `git clone <your team's repository URL>`). Then:

```bash
cd supply-chain-crew
```

Every command in this guide is run from this folder.

## 3. Choose the AI model

Create your settings file and open it:

```bash
cp .env.example .env
nano .env        # or any editor
```

Pick **one** option.

**A) OpenAI (simplest):** put your key in `OPENAI_API_KEY=` and keep `LLM_MODEL=openai/gpt-4o-mini`.

**B) Local Ollama (free, no key):** install Ollama from ollama.com, then pull a model of about 14B parameters or more (smaller ones make malformed tool calls):

```bash
ollama pull qwen2.5:14b
```

In `.env`, comment out the two OpenAI lines and set:

```
LLM_MODEL=ollama/qwen2.5:14b
OLLAMA_BASE_URL=http://localhost:11434
```

Optional: put a free key from serper.dev in `SERPER_API_KEY=` for better news search. Without it the system falls back to DuckDuckGo.

> Tested model: the development team ran everything with the local Ollama model `gemma4:31b-cloud`. The OpenAI setting is supported but was not run by the authors, so if a run misbehaves, try a larger model.

Never share or commit `.env`; it holds your keys.

## 4. One command to set everything up

```bash
bash scripts/bootstrap.sh
```

It creates the Python environment, installs the packages, starts the database and n8n in Docker, creates the tables, installs the four n8n workflows, checks the knowledge base, and runs the whole test suite. It stops with a clear message if something is wrong (for example Docker not running). It is safe to run again.

When it ends with `bootstrap complete`, continue.

## 5. First visit to n8n (once)

1. Open the n8n address in your browser: **http://localhost:5678**. (If you changed ports it is the address in `N8N_WEBHOOK_URL` in `.env`, without the `/webhook/...` part.)
2. n8n asks you to create an owner account. Use any email and password; they stay on your computer.
3. Open **Workflows**. You should see four: *Supply Chain RFQ & Contingency Handler*, *SCM Approval Decision*, *SCM Approval Escalation Tick*, *SCM Error Handler*. All four are active.

## 6. Run it

```bash
# Demo: replays a saved typhoon report. The analyst, coordinator, n8n and approval steps run for real.
.venv/bin/python main.py --no-memory --scout-report samples/scout_typhoon.md
```

It takes a few minutes. When it finishes:
- the log shows `[N8N] Webhook accepted ... event SCD-...`;
- `reports/action_brief.md` is the manager's brief (severity L2, four purchase requests, total ₹5,02,99,000, **CFO approval needed**);
- in n8n open **Executions** to see the workflow run.

A normal live run, where the first agent searches the real news:

```bash
.venv/bin/python main.py --no-memory --simulate-search-failure --focus "typhoon Kaohsiung Keelung port closure"
```

On a quiet news day it correctly reports "no material disruption" and does nothing. Other options:

| Command | What it does |
|---|---|
| `.venv/bin/python main.py --dry-run` | Checks the database, the knowledge base and the settings without calling the AI. |
| `.venv/bin/python main.py --no-memory --watch 30` | Checks every 30 minutes, forever. |
| `.venv/bin/python main.py --replay-dlq` | Retries deliveries that failed earlier (for example while n8n was down). |

## 7. Approve (or watch it escalate)

The purchase requests are **held** until the right approver decides. Get the id of the latest request, look at it, and approve it:

```bash
docker compose exec erp-db psql -U scm -d erp -At -c "select event_id from action_log order by created_at desc limit 1"
.venv/bin/python -m tools.approvals status  SCD-20261003-ABCDEF
.venv/bin/python -m tools.approvals approve SCD-20261003-ABCDEF --as "Chief Financial Officer"
```

(Use your own event id. Levels: `Operations Manager - Procurement`, `Head of Supply Chain Management`, `Chief Financial Officer`. A lower level than the one the rules require is refused.)

If nobody decides: each level gets 8 hours for an L2 event or 4 hours for L3, a reminder at 50%, then the request moves one level up, and after the CFO level it is **HELD** with an alert. n8n checks the clock every 5 minutes. To run the check now: `.venv/bin/python -m tools.approvals tick`. To read reminders and alerts: `.venv/bin/python -m tools.approvals inbox`.

## 8. Optional: email notifications (Gmail)

Reminders, escalations and alerts are always written to the database. To also email them:

1. In Google Cloud Console enable the **Gmail API**, set up the OAuth consent screen (add yourself as a test user), and create an **OAuth client ID of type Web application**. Under *Authorized redirect URIs* add `http://localhost:5678/rest/oauth2-credential/callback` (use your own n8n address if you changed the port; it must say `localhost`, not `127.0.0.1`).
2. In n8n open **Credentials → New → Gmail OAuth2 API**. Set the credential **ID** by importing it, or simply create one named `SCM Gmail`. Paste the Client ID and Secret and click **Sign in with Google**.
3. Say who receives what (nothing is emailed until you do):

```bash
.venv/bin/python -m tools.approvals set-route "Chief Financial Officer" cfo@yourcompany.com
.venv/bin/python -m tools.approvals set-route "SCM Alerts" you@yourcompany.com
.venv/bin/python -m tools.approvals routes
```

Recipients you can route: the three approvers, `Production Planning`, `Procurement`, `SCM Alerts` (n8n failures).

If the Gmail credential does not exist, nothing breaks: messages are only logged.

## 9. Every day

| Task | Command |
|---|---|
| Start | `docker compose up -d` then `.venv/bin/python -m tools.migrate` |
| Stop | `docker compose stop` |
| Check everything works | `.venv/bin/python -m pytest -q` and `.venv/bin/python scripts/chaos_check.py` |
| Wipe all data and start over | `docker compose down -v` then `bash scripts/bootstrap.sh` (you must recreate the n8n owner account and the Gmail credential) |

## 10. If something goes wrong

| What you see | What to do |
|---|---|
| `Docker is installed but not running` | Start Docker Desktop (or `sudo systemctl start docker`) and run the command again. |
| `port is already allocated` (5432 or 5678) | Another program uses it. Create `docker-compose.override.yml` next to `docker-compose.yml` with `services:` / `  erp-db:` / `    ports: !override` / `      - "127.0.0.1:5440:5432"` and the same for `n8n` with `5690:5678`. Then in `.env` set `DATABASE_URL=...@localhost:5440/erp` and `N8N_WEBHOOK_URL=http://localhost:5690/webhook/supply-chain-rfq`. Run `bash scripts/bootstrap.sh` again. |
| `password authentication failed for user "scm"` | You reached a different Postgres. `DATABASE_URL` in `.env` must use the port of this project's database. |
| `could not create a virtual environment` | Ubuntu/Debian: `sudo apt install python3-venv`. |
| `Python 3.10 to 3.13 is required` | Install a supported Python and delete the `.venv` folder first. |
| The run ends with `WARNING - NOT EXECUTED` at the top of the brief | The AI skipped the n8n step. Run the command again (it already retries once); a larger model helps. |
| `no material disruption` | Normal on a quiet news day. Use the `--scout-report samples/scout_typhoon.md` demo. |
| First run is slow | It downloads a small search model once (about 80 MB). |
| n8n shows no workflows | Run `.venv/bin/python scripts/setup_n8n.py`, then refresh the page. Make sure you are on the right address and port. |
| Anything else | Run `.venv/bin/python main.py --dry-run`; it names the first thing that is wrong. |

## 11. Where things are

| Path | What it is |
|---|---|
| `main.py` | the program that runs the three agents |
| `agents/`, `tools/`, `schemas/` | the agents, their tools, and the rule checks |
| `db/` | database schema and migrations |
| `n8n/` | the four workflows (and the scripts that generate them) |
| `knowledge_base/` | the company rule documents (PDF) and their searchable copy |
| `samples/` | the saved typhoon report used for the demo |
| `reports/action_brief.md` | the brief from the last run |
| `docs/` | module notes and `RECOVERY_BLUEPRINT.md` (the full design) |
