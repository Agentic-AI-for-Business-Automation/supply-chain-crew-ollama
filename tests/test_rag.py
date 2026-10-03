import pytest
from tools.rag_tool import build_index, sop_search

SOP, VC = "SOP-SC-014_Supply_Disruption_Response.pdf", "Vendor_Contracts_and_AVL.pdf"
# Queries are phrased like the Coordinator asks: natural question + clause ID / key terms,
# because semantic search is weak on bare IDs like "3.2" or "VC-4.1".
CASES = [
    ("How is the severity of a delay classified? L1 L2 L3 worst-case delay",                 SOP, 1, "L2"),
    ("When is an RFQ to a backup supplier mandatory? days of cover delay + 5 (clause 3.2)",  SOP, 1, "3.2"),
    ("How do we calculate the RFQ quantity? shortfall x 1.2 MOQ (clause 3.3)",               SOP, 1, "1.2"),
    ("Can we send an RFQ to a conditional supplier? Head of SCM sign-off (clause 3.4)",      SOP, 1, "CONDITIONAL"),
    ("When is air freight allowed? premium below 15% (clause 4.1)",                          SOP, 2, "15%"),
    ("How much should safety stock increase? 30% uplift (clause 4.2)",                       SOP, 2, "30%"),
    ("Who approves an order worth 5 crore rupees? approval matrix section 5",                SOP, 2, "Chief Financial Officer"),
    ("What happens to open purchase orders? HOLD-REVIEW (clause 4.4)",                       SOP, 2, "HOLD-REVIEW"),
    ("What are Penang Semicon lead time and price premium? VC-4.1 21 days",                  VC,  1, "Penang"),
    ("Is Pune CellWorks an approved vendor? Schedule A CONDITIONAL",                         VC,  2, "CONDITIONAL"),
]


@pytest.fixture(scope="module", autouse=True)
def index():
    assert build_index(force=True) > 0


@pytest.mark.parametrize("q,doc,page,must_contain", CASES)
def test_retrieves_right_page(q, doc, page, must_contain):
    out = sop_search.run(query=q)
    assert f"[{doc}, page {page}]" in out, out[:300]
    assert must_contain in out


def test_never_raises_on_empty_query():
    assert isinstance(sop_search.run(query=""), str)


def test_index_is_reused_when_unchanged():
    n = build_index(force=True)
    assert build_index() == n          # second call re-uses, same chunk count