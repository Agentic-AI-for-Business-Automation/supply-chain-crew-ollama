import copy, os, re
import psycopg2, pytest
from tools import kb_lint
from schemas import policy_constants as pc
from schemas.policy_check import check

JS = os.path.join(os.path.dirname(__file__), "..", "n8n", "js", "validate_build.js")


def test_inr_grouping():
    assert [kb_lint.inr(n) for n in (2_500_000, 2_500_001, 10_000_000, 999)] == ["25,00,000", "25,00,001", "1,00,00,000", "999"]


def test_real_knowledge_base_is_clean():
    assert kb_lint.lint(kb_lint.load_records()) == []


def test_lint_against_the_live_erp_ids():
    dsn = os.getenv("DATABASE_URL", "").replace("+psycopg2", "")
    try:
        c = psycopg2.connect(dsn, connect_timeout=3)
    except Exception as e:
        pytest.skip(str(e))
    cur = c.cursor()
    cur.execute("SELECT supplier_id FROM suppliers"); sup = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT part_id FROM parts"); parts = {r[0] for r in cur.fetchall()}
    c.close()
    assert kb_lint.lint(kb_lint.load_records(), sup, parts) == []


def test_lint_catches_each_class_of_defect():
    recs = kb_lint.load_records()
    dup = recs + [copy.deepcopy(recs[3])]
    assert any("duplicate clause id" in e for e in kb_lint.lint(dup))
    dangling = copy.deepcopy(recs)
    next(r for r in dangling if r["clause"] == "3.4")["text"] += " See clause 9.9."
    assert any("clause 9.9" in e for e in kb_lint.lint(dangling))
    wrong = copy.deepcopy(recs)
    r = next(r for r in wrong if r["clause"] == "4.2"); r["text"] = r["text"].replace("30%", "35%")
    assert any("safety stock uplift" in e for e in kb_lint.lint(wrong))
    wrong = copy.deepcopy(recs)
    r = next(r for r in wrong if r["clause"] == "Section 5 table"); r["text"] = r["text"].replace("1,00,00,000", "2,00,00,000")
    assert any("approval" in e for e in kb_lint.lint(wrong))
    assert any("S999" in e for e in kb_lint.lint(recs + [dict(recs[1], clause="1.9", text="1.9 Use S999.")], {"S101"}, None))


def test_n8n_javascript_thresholds_match_policy_constants():
    js = open(JS, encoding="utf-8").read()
    m = re.search(r"total <= (\d+) \? '([^']+)' : total <= (\d+) \? '([^']+)' : '([^']+)'", js)
    assert m, "approver ladder not found in validate_build.js"
    assert (int(m.group(1)), int(m.group(3))) == (pc.OPS_LIMIT_INR, pc.HEAD_LIMIT_INR)
    assert (m.group(2), m.group(4), m.group(5)) == pc.APPROVERS
    assert re.search(r"const APPROVERS = \['([^']+)', '([^']+)', '([^']+)'\]", js).groups() == pc.APPROVERS


def test_clause_level_citation_is_checked():
    erp = {("S201", "P-1001"): dict(role="APPROVED_BACKUP", price=452.0, moq=2000, capacity=1, avl="APPROVED", email="a@b.co"),
           "parts": {"P-1001": dict(cover=12.0, daily=1200)}}
    SOP = "SOP-SC-014_Supply_Disruption_Response.pdf"
    base = dict(severity="L2", approval_authority=pc.APPROVER_HEAD, sources=[], affected_parts=[dict(part_id="P-1001", projected_delay_days=21)],
                rfqs=[dict(supplier_id="S201", part_id="P-1001", quantity=22000, unit_price_inr=452.0, est_value_inr=9_944_000, supplier_email="")],
                policy_citations=[dict(document=SOP, page=1, clause="3.2")])
    got = {(SOP, 1, "3.2"), (SOP, 1, "3")}
    assert check(copy.deepcopy(base), erp, {(SOP, 1)}, got) == []
    bad = copy.deepcopy(base); bad["policy_citations"] = [dict(document=SOP, page=1, clause="9.9")]
    assert any("clause '9.9'" in e for e in check(bad, erp, {(SOP, 1)}, got))
    assert check(copy.deepcopy(base), erp, {(SOP, 1)}, None) == []      # clause check only when clause tracking is available
