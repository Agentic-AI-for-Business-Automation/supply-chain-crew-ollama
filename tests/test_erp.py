from tests.dbconf import admin_dsn
import os, math, psycopg2, pytest
from dotenv import load_dotenv
load_dotenv()
DSN = os.getenv("DATABASE_URL", "postgresql+psycopg2://scm:scm@localhost:5432/erp").replace("+psycopg2", "")

@pytest.fixture(scope="module")
def cur():
    try:
        conn = psycopg2.connect(DSN, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f"ERP database not running: {e}")
    yield conn.cursor()
    conn.close()

def test_seven_parts_with_expected_cover(cur):
    cur.execute("SELECT part_id, days_of_cover FROM v_part_risk ORDER BY part_id")
    got = {p: float(d) for p, d in cur.fetchall()}
    assert got == {"P-1001": 12.0, "P-1002": 21.0, "P-2001": 18.0, "P-3001": 35.0,
                   "P-3002": 8.0, "P-4001": 40.0, "P-5001": 25.0}

def test_backups_exist_for_taiwan_parts(cur):
    cur.execute("""SELECT part_id, supplier_id FROM supplier_parts
                   WHERE sourcing_role='APPROVED_BACKUP' ORDER BY 1,2""")
    rows = cur.fetchall()
    assert ("P-1001", "S201") in rows and ("P-3002", "S301") in rows and ("P-2001", "S202") in rows

def test_open_pos_on_taiwan_routes(cur):
    cur.execute("SELECT count(*) FROM purchase_orders WHERE status IN ('CONFIRMED','IN_TRANSIT') "
                "AND (shipping_route LIKE 'Kaohsiung%' OR shipping_route LIKE 'Keelung%')")
    assert cur.fetchone()[0] == 5

def test_rfq_math_matches_A5_demo(cur):
    """End-to-end data-quality gate: SOP 3.3 qty = ceil(shortfall*1.2/MOQ)*MOQ, delay=21."""
    cur.execute("""SELECT i.part_id, i.on_hand_qty, i.daily_consumption,
                          sp.supplier_id, sp.unit_price_inr, sp.moq
                   FROM inventory i JOIN supplier_parts sp USING (part_id)
                   WHERE sp.sourcing_role='APPROVED_BACKUP'
                   AND ((i.part_id='P-1001' AND sp.supplier_id='S201')
                     OR (i.part_id='P-1002' AND sp.supplier_id='S201')
                     OR (i.part_id='P-2001' AND sp.supplier_id='S202')
                     OR (i.part_id='P-3002' AND sp.supplier_id='S301'))""")
    expect = {"P-1001": (16800, 22000, 9944000), "P-1002": (12000, 15000, 9825000),
              "P-2001": (9600, 12000, 11160000), "P-3002": (21600, 26000, 19370000)}
    total = 0
    for pid, onhand, daily, sup, price, moq in cur.fetchall():
        cover = onhand / daily
        gap = 21 + 5 - cover
        short = int(gap * daily)
        qty = math.ceil(short * 1.2 / moq) * moq
        exp_short, exp_qty, exp_val = expect[pid]
        assert short == exp_short, pid
        assert qty == exp_qty, pid
        assert qty * float(price) == exp_val, pid
        total += qty * float(price)
    assert total == 50299000  # -> CFO approval (>1cr)

def test_conditional_supplier_flagged(cur):
    cur.execute("SELECT avl_status FROM suppliers WHERE supplier_id='S402'")
    assert cur.fetchone()[0] == 'CONDITIONAL'

def test_po_values_match_price_x_qty(cur):
    cur.execute("""SELECT po_id, quantity*q.unit_price_inr, po_value_inr FROM purchase_orders po
                   JOIN supplier_parts q USING (supplier_id, part_id)
                   WHERE po.supplier_id IN ('S101','S102','S103')""")
    for po, calc, stored in cur.fetchall():
        assert abs(float(calc) - float(stored)) < 1.0, po

def test_p4001_stays_single_source(cur):
    """Part B+ 1: P-4001 must have exactly one supplier (S302, PRIMARY) and no backup."""
    cur.execute("SELECT supplier_id, sourcing_role FROM supplier_parts WHERE part_id='P-4001'")
    assert cur.fetchall() == [("S302", "PRIMARY")]


# ---- structural constraints (run inside a rolled-back transaction with an admin connection) ----
ADMIN_DSN = admin_dsn()


@pytest.fixture()
def admin():
    try:
        conn = psycopg2.connect(ADMIN_DSN, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f"admin connection unavailable (set ERP_ADMIN_URL): {e}")
    yield conn
    conn.rollback()
    conn.close()


def _fails(conn, sql, params=None):
    import psycopg2.errors as pe
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    try:
        cur.execute(sql, params)
    except (pe.IntegrityError, pe.CheckViolation) as e:
        cur.execute("ROLLBACK TO SAVEPOINT s")
        return type(e).__name__
    cur.execute("ROLLBACK TO SAVEPOINT s")
    return None


def test_second_primary_for_a_part_is_rejected(admin):
    assert _fails(admin, "INSERT INTO supplier_parts VALUES ('S201','P-4001','PRIMARY',40,1000,5000)") == "UniqueViolation"


def test_po_with_null_supplier_or_part_is_rejected(admin):
    base = ("INSERT INTO purchase_orders (po_id,supplier_id,part_id,quantity,unit_price_inr,po_value_inr,order_date,"
            "expected_delivery,origin_port,dest_port,transport_mode,status) VALUES ('PO-26-9999',%s,%s,10,10,100,"
            "CURRENT_DATE,CURRENT_DATE,'Kaohsiung','Nhava Sheva','SEA','CONFIRMED')")
    assert _fails(admin, base, (None, "P-1001")) == "NotNullViolation"
    assert _fails(admin, base, ("S101", None)) == "NotNullViolation"


def test_po_for_supplier_part_pair_that_does_not_exist_is_rejected(admin):
    sql = ("INSERT INTO purchase_orders (po_id,supplier_id,part_id,quantity,unit_price_inr,po_value_inr,order_date,"
           "expected_delivery,origin_port,dest_port,transport_mode,status) VALUES ('PO-26-9998','S101','P-5001',10,10,100,"
           "CURRENT_DATE,CURRENT_DATE,'Kaohsiung','Nhava Sheva','SEA','CONFIRMED')")
    assert _fails(admin, sql) == "ForeignKeyViolation"


def test_po_value_must_equal_qty_times_price(admin):
    sql = ("INSERT INTO purchase_orders (po_id,supplier_id,part_id,quantity,unit_price_inr,po_value_inr,order_date,"
           "expected_delivery,origin_port,dest_port,transport_mode,status) VALUES ('PO-26-9997','S101','P-1001',10,420,999,"
           "CURRENT_DATE,CURRENT_DATE,'Kaohsiung','Nhava Sheva','SEA','CONFIRMED')")
    assert _fails(admin, sql) == "CheckViolation"


def test_shipping_route_is_generated_and_filterable(cur):
    cur.execute("SELECT count(*) FROM purchase_orders WHERE shipping_route = origin_port || ' -> ' || dest_port || ' (' || lower(transport_mode) || ')'")
    assert cur.fetchone()[0] == 7


def test_view_keeps_part_without_primary_visible(admin):
    cur = admin.cursor()
    cur.execute("INSERT INTO parts VALUES ('P-9999','Orphan part','Test',FALSE,'EA')")
    cur.execute("INSERT INTO inventory (part_id,warehouse,on_hand_qty,safety_stock_qty,daily_consumption) VALUES ('P-9999','Manesar-WH1',10,1,1)")
    cur.execute("SELECT primary_supplier_id FROM v_part_risk WHERE part_id='P-9999'")
    assert cur.fetchall() == [(None,)]


def test_eligible_view_flags_conditional_supplier(cur):
    cur.execute("SELECT supplier_id, rfq_allowed_without_signoff FROM v_eligible_backups WHERE supplier_id='S402'")
    assert cur.fetchall() == [("S402", False)]


def test_readonly_role_can_read_new_tables_but_not_write():
    dsn = os.getenv("DATABASE_URL", "").replace("+psycopg2", "")
    if "scm_ro" not in dsn:
        pytest.skip("DATABASE_URL does not use scm_ro")
    conn = psycopg2.connect(dsn); c = conn.cursor()
    c.execute("SELECT count(*) FROM action_log"); c.execute("SELECT count(*) FROM v_eligible_backups")
    with pytest.raises(psycopg2.Error):
        c.execute("DELETE FROM parts")
    conn.close()
