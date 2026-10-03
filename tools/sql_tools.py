"""ERP (PostgreSQL) tools for the Inventory Impact Analyst."""
import os, re
from datetime import datetime
from crewai.tools import tool
from dotenv import load_dotenv
from langchain_community.utilities import SQLDatabase

load_dotenv()
def _db_uri() -> str:
    return os.getenv("DATABASE_URL", "postgresql+psycopg2://scm:scm@localhost:5432/erp")
_db = None
# Block writes even when hidden in stacked queries or string tricks. String literals are
# stripped before the check so a part named e.g. 'Updated Bracket' does not false-positive,
# while 'SELECT 1; DROP TABLE' still gets rejected via the DROP keyword.
_BLOCKED = re.compile(r"(?i)\b(insert|update|delete|drop|alter|truncate|grant|create|copy|call)\b")
_CLAUSE_SPLIT = re.compile(r"'(?:''|[^'])*'")  # single-quoted literals


def log(tag: str, msg: str) -> None:
    print(f"\033[96m[{datetime.now():%H:%M:%S}] [{tag}]\033[0m {msg}", flush=True)


def get_db() -> SQLDatabase:
    global _db
    if _db is None:
        _db = SQLDatabase.from_uri(_db_uri(), view_support=True, sample_rows_in_table_info=2)
    return _db


def get_watchlist() -> str:
    rows = get_db()._execute(
        "SELECT DISTINCT s.supplier_name, s.country, s.export_port FROM suppliers s "
        "JOIN supplier_parts sp USING (supplier_id) WHERE sp.sourcing_role = 'PRIMARY' "
        "ORDER BY s.country, s.supplier_name")
    return "; ".join(f"{r['supplier_name']} ({r['country']}, port {r['export_port']})" for r in rows)


@tool("List ERP Tables")
def list_erp_tables(dummy: str = "") -> str:
    """List all tables and views in the company ERP (PostgreSQL). Call this FIRST."""
    try:
        return ", ".join(get_db().get_usable_table_names())
    except Exception as e:
        return f"ERROR: cannot reach the ERP database ({e})."


@tool("Describe ERP Tables")
def describe_erp_tables(table_names: str) -> str:
    """Show columns (CREATE statement) and 2 sample rows for tables.
    Input: comma-separated names, e.g. 'suppliers, inventory'. Use before writing SQL."""
    try:
        return get_db().get_table_info([t.strip() for t in table_names.split(",") if t.strip()])
    except Exception as e:
        return f"ERROR: {e}. Use 'List ERP Tables' to get valid names."


@tool("Query ERP Database")
def query_erp(sql: str) -> str:
    """Run ONE read-only PostgreSQL SELECT (or WITH ... SELECT) and get rows with column names.
    Write your own joins across suppliers, parts, supplier_parts, inventory, purchase_orders, v_part_risk.
    TIP: do arithmetic in SQL for exact numbers, e.g.
    SELECT part_id, ROUND(on_hand_qty::numeric/daily_consumption,1) AS cover,
           (21+5 - on_hand_qty::numeric/daily_consumption) AS gap_days,
           GREATEST(0,(21+5 - on_hand_qty::numeric/daily_consumption))*daily_consumption AS shortfall
    FROM v_part_risk ORDER BY part_id; Results are capped at 100 rows / 6000 chars."""
    sql = (sql or "").strip().strip("`").strip()
    if sql.lower().startswith("sql"):
        sql = sql[3:].strip().lstrip(":").strip()
    sql = sql.rstrip(";").strip()
    if len(sql) > 8000:
        return "REJECTED: query too long (>8000 chars). Select fewer columns."
    code_only = _CLAUSE_SPLIT.sub("''", sql)  # ignore keywords inside '...' literals
    if ";" in code_only:
        return "REJECTED: only ONE statement allowed (no semicolons)."
    if not re.match(r"(?is)^\s*(select|with)\b", code_only) or _BLOCKED.search(code_only):
        return "REJECTED: only read-only SELECT/WITH queries are allowed."
    # Deterministic guardrail: force a LIMIT so a SELECT * cannot blow the prompt
    if not re.search(r"(?i)\blimit\s+\d+", code_only):
        sql += " LIMIT 100"
    try:
        out = get_db().run(sql, include_columns=True)
        log("TOOL", f"SQL OK: {sql[:110]}")
        return (out[:6000] if out else "0 rows")
    except Exception as e:
        log("RECOVERY", f"SQL error returned to agent: {str(e)[:120]}")
        return (f"SQL ERROR: {str(e)[:500]}\n"
                "Fix the query (check names with 'Describe ERP Tables') and retry.")


if __name__ == "__main__":   # quick check: python -m tools.sql_tools
    print(list_erp_tables.run(dummy=""))
    print(get_watchlist())
    print(query_erp.run(sql="SELECT * FROM v_part_risk ORDER BY days_of_cover"))
    print(query_erp.run(sql="SELECT wrong_column FROM parts"))   # should show SQL ERROR
    print(query_erp.run(sql="DROP TABLE parts"))                  # should show REJECTED
