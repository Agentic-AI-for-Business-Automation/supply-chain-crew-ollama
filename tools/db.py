"""One pooled SQLAlchemy engine per role. pool_pre_ping makes a restarted database transparent to long-running loops."""
import os
import threading

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.engine import Engine

load_dotenv()
_LOCK = threading.Lock()
_ENGINES: dict[str, Engine] = {}

_ENV = {"ro": "DATABASE_URL", "audit": "AUDIT_DATABASE_URL", "migrate": "MIGRATE_DATABASE_URL"}
_OPTIONS = {   # session guards applied at connect time, independent of any application-level check
    "ro": "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000",
    "audit": "-c statement_timeout=5000 -c lock_timeout=2000",
    "migrate": "-c statement_timeout=120000",
}


def _derived(role: str) -> str:
    """Same host, port and database as DATABASE_URL, with the role's own login. This keeps every role on the database the
    project actually uses (for example a remapped port) when only DATABASE_URL is configured."""
    base = make_url(os.getenv("DATABASE_URL") or "postgresql+psycopg2://scm_ro:scm_ro@localhost:5432/erp")
    user, pw = {"audit": ("scm_audit", os.getenv("AUDIT_DB_PASSWORD", "scm_audit")),
                "migrate": (os.getenv("MIGRATE_DB_USER", "scm"), os.getenv("MIGRATE_DB_PASSWORD", "scm"))}[role]
    return base.set(username=user, password=pw).render_as_string(hide_password=False)


def _url(role: str) -> str:
    """Explicit variable first (MIGRATE_DATABASE_URL, then ERP_ADMIN_URL for migrations), otherwise derived from DATABASE_URL."""
    url = os.getenv(_ENV[role])
    if not url and role == "migrate":
        url = os.getenv("ERP_ADMIN_URL")
    if not url:
        url = _derived(role) if role != "ro" else "postgresql+psycopg2://scm_ro:scm_ro@localhost:5432/erp"
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def describe(role: str) -> str:
    """host:port/database as user, for error messages (never the password)."""
    u = make_url(_url(role))
    return f"{u.host}:{u.port}/{u.database} as {u.username}"


def get_engine(role: str = "ro") -> Engine:
    """role: 'ro' (agents, read-only), 'audit' (action_log / dead_letter writer), 'migrate' (DDL)."""
    url = _url(role)
    key = f"{role}|{url}"
    with _LOCK:
        if key not in _ENGINES:
            _ENGINES[key] = create_engine(
                url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_recycle=1800, pool_timeout=10,
                connect_args={"options": _OPTIONS[role], "connect_timeout": 5})
        return _ENGINES[key]


def reset_engines() -> None:
    """Dispose every pool (tests, or after changing environment variables)."""
    with _LOCK:
        for e in _ENGINES.values():
            e.dispose()
        _ENGINES.clear()
