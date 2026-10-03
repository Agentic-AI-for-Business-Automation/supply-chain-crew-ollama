#!/usr/bin/env bash
# One command from a clean checkout to a verified system:  bash scripts/bootstrap.sh
# Needs: Docker (with Compose v2), Python 3.10-3.13 with venv, internet on the first run. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' is not installed or not on PATH. See docs/SETUP_GUIDE.md section 1."; exit 1; }; }
need docker; need python3
docker info >/dev/null 2>&1 || { echo "ERROR: Docker is installed but not running. Start Docker Desktop / the docker service and retry."; exit 1; }
python3 - <<'PY' || { echo "ERROR: Python 3.10 to 3.13 is required (crewai 1.15.23 does not support other versions)."; exit 1; }
import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)
PY

[ -d .venv ] || python3 -m venv .venv || { echo "ERROR: could not create a virtual environment (on Ubuntu: sudo apt install python3-venv)."; exit 1; }
PY=.venv/bin/python
$PY -m pip install -q --upgrade pip
$PY -m pip install -q -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo "created .env from .env.example - edit it to choose your model (docs/SETUP_GUIDE.md section 4)"; fi
set -a; . ./.env; set +a

docker compose up -d
echo "waiting for the database..."
DB=$(docker compose ps -a -q erp-db)
for i in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$DB" 2>/dev/null || echo none)" = healthy ] && break
  sleep 2
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "$DB" 2>/dev/null || echo none)" = healthy ] || { echo "ERROR: the database did not become healthy (docker compose logs erp-db)."; exit 1; }

$PY -m tools.migrate
$PY scripts/setup_n8n.py
$PY -m tools.kb_lint
$PY main.py --dry-run
$PY -m pytest -q
echo
echo "bootstrap complete. Next: open n8n (URL in N8N_WEBHOOK_URL, without the /webhook/... part) and create the owner account,"
echo "then run:  .venv/bin/python main.py --no-memory --scout-report samples/scout_typhoon.md"
