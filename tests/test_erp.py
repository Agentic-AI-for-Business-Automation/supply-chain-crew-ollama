import os, math, psycopg2, pytest
from dotenv import load_dotenv
load_dotenv()
DSN = os.getenv("DATABASE_URL", "postgresql+psycopg2://scm:scm@localhost:5432/erp").replace("+psycopg2", "")

@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(DSN)
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
