"""Apply pending db/migrations/V*.sql in order. Safe to run any number of times and from several processes at once.
Run:  python -m tools.migrate        (uses MIGRATE_DATABASE_URL, falling back to ERP_ADMIN_URL)"""
import glob, hashlib, os, re
from datetime import datetime

from sqlalchemy import text

from schemas import policy_constants as pc
from tools.db import get_engine

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
BASELINE = ("V001", "db/init.sql")                       # the original one-shot schema
LOCK_ID = 7_340_217                                       # arbitrary application-wide advisory lock key


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


def _checksum(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _migration_files() -> list[tuple[str, str]]:
    found = []
    for p in sorted(glob.glob(os.path.join(ROOT, "db", "migrations", "V*.sql"))):
        m = re.match(r"(V\d+)__", os.path.basename(p))
        if not m:
            raise ValueError(f"migration file name must look like V003__description.sql: {p}")
        found.append((m.group(1), p))
    return found


def sync_policy(conn) -> list[str]:
    """Make the approval ladder and windows stored in the database equal schemas/policy_constants.py (the single source of
    truth). Seeds in applied migrations are never edited; changing a number in the constants and running this is enough.
    Open approvals keep the deadline they were created with; the new windows apply to actions accepted afterwards."""
    if not conn.execute(text("SELECT to_regclass('public.approval_policy') IS NOT NULL")).scalar():
        return []                                            # V003 not applied yet
    changes: list[str] = []
    current = {r[0]: (r[1], r[2]) for r in conn.execute(text("SELECT severity, window_minutes, reminder_pct FROM approval_policy"))}
    for sev, hours in sorted(pc.APPROVAL_WINDOW_HOURS.items()):
        want = (hours * 60, pc.APPROVAL_REMINDER_PCT)
        if current.get(sev) != want:
            conn.execute(text("INSERT INTO approval_policy (severity, window_minutes, reminder_pct) VALUES (:s, :w, :r) "
                              "ON CONFLICT (severity) DO UPDATE SET window_minutes = EXCLUDED.window_minutes, reminder_pct = EXCLUDED.reminder_pct"),
                         {"s": sev, "w": want[0], "r": want[1]})
            changes.append(f"approval_policy {sev}: {current.get(sev)} -> {want}")
    names = {r[0]: r[1] for r in conn.execute(text("SELECT rank, name FROM approval_levels"))}
    for rank, name in enumerate(pc.APPROVAL_LADDER, 1):
        if names.get(rank) != name:
            conn.execute(text("INSERT INTO approval_levels (rank, name) VALUES (:r, :n) ON CONFLICT (rank) DO UPDATE SET name = EXCLUDED.name"),
                         {"r": rank, "n": name})
            changes.append(f"approval_levels {rank}: {names.get(rank)} -> {name}")
    return changes


def migrate() -> list[str]:
    """Returns the versions applied by this call (empty when the database is already current)."""
    applied_now: list[str] = []
    with get_engine("migrate").connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_ID})
        try:
            conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations ("
                              "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"))
            done = {r[0]: r[1] for r in conn.execute(text("SELECT version, checksum FROM schema_migrations"))}
            base_path = os.path.join(ROOT, BASELINE[1])
            if BASELINE[0] not in done:
                exists = conn.execute(text("SELECT to_regclass('public.suppliers') IS NOT NULL")).scalar()
                if not exists:
                    raise RuntimeError("baseline schema missing: start the database from db/init.sql first")
                conn.execute(text("INSERT INTO schema_migrations VALUES (:v, :c)"), {"v": BASELINE[0], "c": _checksum(base_path)})
                applied_now.append(BASELINE[0])
                log("BOOT", f"{BASELINE[0]} baseline recorded")
            elif done[BASELINE[0]] != _checksum(base_path):
                raise RuntimeError("db/init.sql changed after it was applied: write a new V00N migration instead of editing it")
            for version, path in _migration_files():
                digest = _checksum(path)
                if version in done:
                    if done[version] != digest:
                        raise RuntimeError(f"{os.path.basename(path)} was modified after being applied; add a new migration instead")
                    continue
                with open(path, encoding="utf-8") as f:
                    sql = f.read()
                tx = conn.connection.cursor()                  # one transaction per file (psycopg2 cursor)
                conn.connection.autocommit = False
                try:
                    tx.execute(sql)
                    tx.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (version, digest))
                    conn.connection.commit()
                except Exception:
                    conn.connection.rollback()
                    raise
                finally:
                    conn.connection.autocommit = True
                applied_now.append(version)
                log("BOOT", f"migration {version} applied")
            for change in sync_policy(conn):
                log("BOOT", f"policy synced from policy_constants: {change}")
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
    return applied_now


if __name__ == "__main__":
    import sys
    from sqlalchemy.exc import OperationalError
    from tools.db import describe
    try:
        done = migrate()
    except OperationalError as e:
        first = str(e.orig).strip().splitlines()[0] if getattr(e, "orig", None) else "connection failed"
        print(f"ERROR: cannot connect to the migration database ({describe('migrate')}): {first}\n"
              "Check that the project's database container is running and that DATABASE_URL in .env points at it (same host and port).\n"
              "Set MIGRATE_DATABASE_URL (or MIGRATE_DB_USER / MIGRATE_DB_PASSWORD) if the admin login is not scm/scm.", file=sys.stderr)
        sys.exit(2)
    print("applied:", done or "nothing (database is current)")
