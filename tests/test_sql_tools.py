import pytest
from tools import sql_tools as s

def _db_up():
    try: s.get_db(); return True
    except Exception: return False
needs_db = pytest.mark.skipif(not _db_up(), reason="ERP database not running")

@pytest.mark.parametrize("bad", ["DROP TABLE parts", "delete from parts", "UPDATE parts SET uom='x'",
                                 "SELECT 1; DROP TABLE parts", "CREATE TABLE x(a int)",
                                 "SELECT * FROM parts; DELETE FROM parts", "COPY parts TO '/tmp/x'",
                                 "SELECT * INTO scratch FROM parts", "SELECT pg_sleep(600)",
                                 "SELECT pg_read_file('/etc/passwd')", "SELECT nextval('x')",
                                 "SELECT pg_terminate_backend(1)"])
def test_guard_rejects_writes(bad):
    assert s.query_erp.run(sql=bad).startswith("REJECTED")

def test_guard_ignores_keywords_inside_literals():
    # 'Updated ...' contains 'update' as substring but must NOT trip the write-guard.
    out = s.query_erp.run(sql="SELECT * FROM parts WHERE part_name='Updated Bracket' LIMIT 1")
    assert not out.startswith("REJECTED")

@needs_db
def test_lists_expected_tables():
    out = s.list_erp_tables.run(dummy="")
    for t in ["suppliers", "parts", "supplier_parts", "inventory", "purchase_orders"]:
        assert t in out

@needs_db
def test_bad_column_returns_sql_error_not_crash():
    assert s.query_erp.run(sql="SELECT no_such_col FROM parts").startswith("SQL ERROR")

@needs_db
def test_view_numbers():
    out = s.query_erp.run(sql="SELECT part_id, days_of_cover FROM v_part_risk WHERE part_id='P-1001'")
    assert "12" in out

@needs_db
def test_watchlist_has_taiwan_suppliers():
    w = s.get_watchlist()
    assert "Formosa Microchip Co." in w and "Kaohsiung" in w and "Keelung" in w
