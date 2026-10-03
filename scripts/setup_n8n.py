"""Install the n8n side with no UI clicks: credentials, both workflows, publish, restart, health check.
Run:  python scripts/setup_n8n.py        (reads .env; creates N8N_WEBHOOK_TOKEN in .env when it is missing)"""
import json, os, secrets, subprocess, sys, tempfile, time
import urllib.error, urllib.request

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _compose import container as _container
CONTAINER = None   # resolved in main(): the n8n service of this compose project
def _base() -> str:
    """n8n origin: N8N_BASE_URL, else the origin of N8N_WEBHOOK_URL (so a remapped port in .env is honoured), else the default."""
    if os.getenv("N8N_BASE_URL"):
        return os.environ["N8N_BASE_URL"].rstrip("/")
    hook = load_env().get("N8N_WEBHOOK_URL") or os.getenv("N8N_WEBHOOK_URL")
    if hook:
        from urllib.parse import urlparse
        u = urlparse(hook)
        return f"{u.scheme}://{u.netloc}"
    return "http://127.0.0.1:5678"


BASE = None


def sh(*args: str, check=True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"command failed: {' '.join(args)}\n{r.stdout}\n{r.stderr}")
    return r.stdout + r.stderr


def env_file() -> str:
    return os.path.join(ROOT, ".env")


def load_env() -> dict:
    out = {}
    if os.path.exists(env_file()):
        for line in open(env_file(), encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                out[k.strip()] = v.strip()
    return out


def ensure_token(env: dict) -> str:
    token = os.getenv("N8N_WEBHOOK_TOKEN") or env.get("N8N_WEBHOOK_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        with open(env_file(), "a", encoding="utf-8") as f:
            f.write(f"\nN8N_WEBHOOK_TOKEN={token}\n")
        print("generated N8N_WEBHOOK_TOKEN and stored it in .env")
    return token


def main() -> None:
    global BASE, CONTAINER
    BASE = _base()
    CONTAINER = _container("n8n", "N8N_CONTAINER", "n8n")
    env = load_env()
    token = ensure_token(env)
    audit_pw = os.getenv("AUDIT_DB_PASSWORD", env.get("AUDIT_DB_PASSWORD", "scm_audit"))
    creds = [
        {"id": "scmwebhooktoken01", "name": "SCM webhook token", "type": "httpHeaderAuth",
         "data": {"name": "X-SCM-Token", "value": token}},
        {"id": "scmpostgres00001", "name": "SCM audit DB", "type": "postgres",
         "data": {"host": os.getenv("N8N_DB_HOST", "erp-db"), "port": 5432, "database": "erp", "user": "scm_audit",
                  "password": audit_pw, "ssl": "disable", "allowUnauthorizedCerts": False, "maxConnections": 5, "sshTunnel": False}},
    ]
    with tempfile.TemporaryDirectory() as td:
        cpath = os.path.join(td, "credentials.json")
        json.dump(creds, open(cpath, "w"))
        sh("docker", "cp", cpath, f"{CONTAINER}:/tmp/scm_credentials.json")
    sh("docker", "exec", CONTAINER, "n8n", "import:credentials", "--input=/tmp/scm_credentials.json")
    sh("docker", "exec", "-u", "root", CONTAINER, "rm", "-f", "/tmp/scm_credentials.json")      # the token never stays on disk in plain text
    workflows = [("error_handler_workflow.json", "scmerrorhandler001"), ("supply_chain_workflow.json", "scmrfqworkflow0001"),
                 ("approval_decision_workflow.json", "scmapprovaldecide01"), ("approval_tick_workflow.json", "scmapprovaltick0001")]
    for f, _ in workflows:
        sh("docker", "cp", os.path.join(ROOT, "n8n", f), f"{CONTAINER}:/tmp/{f}")
        print(sh("docker", "exec", CONTAINER, "n8n", "import:workflow", f"--input=/tmp/{f}").strip().splitlines()[-1])
    for _, wid in workflows:
        sh("docker", "exec", CONTAINER, "n8n", "publish:workflow", f"--id={wid}")
    sh("docker", "restart", CONTAINER)
    hook = f"{BASE}/webhook/supply-chain-rfq"
    for _ in range(60):                       # healthz answers before workflows are active: wait for the webhook itself
        try:
            urllib.request.urlopen(urllib.request.Request(hook, data=b"{}", headers={"Content-Type": "application/json"}), timeout=3)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):          # registered and protected by the header credential
                print("n8n is ready; webhook protected by header X-SCM-Token")
                return
        except Exception:
            pass
        time.sleep(2)
    raise SystemExit("the n8n webhook did not register within 2 minutes (check `docker logs` and the credentials)")


if __name__ == "__main__":
    main()
