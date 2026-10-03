"""The ERP, the contract prose, Schedule A and the .docx originals must never drift apart."""
import html, os, re, zipfile
import psycopg2, pytest
from pypdf import PdfReader

KB = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
SOP, VC = "SOP-SC-014_Supply_Disruption_Response", "Vendor_Contracts_and_AVL"
ESC = "SOP-SC-015_Approval_Timeouts_and_Escalation"


def pdf_text(name):
    return "\n".join(p.extract_text() or "" for p in PdfReader(os.path.join(KB, f"{name}.pdf")).pages)


@pytest.fixture(scope="module")
def cur():
    dsn = os.getenv("DATABASE_URL", "postgresql+psycopg2://scm:scm@localhost:5432/erp").replace("+psycopg2", "")
    try:
        conn = psycopg2.connect(dsn, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f"ERP database not running: {e}")
    yield conn.cursor()
    conn.close()


def test_schedule_a_matches_erp_avl_status(cur):
    vc = re.sub(r"\s+", " ", pdf_text(VC))
    cur.execute("SELECT supplier_id, avl_status FROM suppliers ORDER BY 1")
    rows = cur.fetchall()
    assert len(rows) == 9
    for sid, status in rows:
        assert re.search(rf"{sid} .{{0,60}}? {status}\b", vc), f"Schedule A disagrees with ERP for {sid} ({status})"


def test_contract_lead_times_match_erp(cur):
    vc = re.sub(r"\s+", " ", pdf_text(VC))
    cur.execute("SELECT supplier_id, standard_lead_days FROM suppliers WHERE supplier_id IN ('S101','S102','S201','S202','S301')")
    for sid, days in cur.fetchall():
        clause = re.search(rf"\({sid}\).*?(?=VC-\d\.\d|$)", vc)
        assert clause and re.search(rf"\b{days} days\b", clause.group(0)), f"{sid}: contract text disagrees with ERP lead time {days}"


def test_every_part_and_supplier_id_in_the_kb_exists_in_the_erp(cur):
    cur.execute("SELECT supplier_id FROM suppliers"); sup = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT part_id FROM parts"); parts = {r[0] for r in cur.fetchall()}
    text = pdf_text(SOP) + pdf_text(VC)
    assert set(re.findall(r"\bS\d{3}\b", text)) <= sup
    assert set(re.findall(r"\bP-\d{4}\b", text)) <= parts


@pytest.mark.parametrize("name", [SOP, VC, ESC])
def test_docx_original_has_same_words_as_pdf(name):
    docx = os.path.join(KB, "source", f"{name}.docx")
    xml = html.unescape(re.sub(r"<[^>]+>", " ", zipfile.ZipFile(docx).read("word/document.xml").decode()))
    words = lambda s: set(re.findall(r"[\w.%-]+", s))
    assert words(xml) == words(pdf_text(name))


def test_sop_is_two_pages_and_clarifications_present():
    assert len(PdfReader(os.path.join(KB, f"{SOP}.pdf")).pages) == 2
    t = re.sub(r"\s+", " ", pdf_text(SOP))
    assert "2.3 An event is confirmed only when two independent sources" in t
    assert "equal to or above projected delay + 5 days" in t
    assert "freight quote required" in t
    assert "4.7 Lead time check" in t and "expected stock-out gap" in t
    assert "falls before the earliest backup supplier can deliver (clause 4.7)" in t


def test_escalation_sop_is_one_page_and_states_the_no_bypass_rule():
    assert len(PdfReader(os.path.join(KB, f"{ESC}.pdf")).pages) == 1
    t = re.sub(r"\s+", " ", pdf_text(ESC))
    assert "no automatic approval and no bypass of an approver" in t and "the action record is HELD" in t
    assert "Operations Manager - Procurement, then Head of Supply Chain Management, then Chief Financial Officer" in t
