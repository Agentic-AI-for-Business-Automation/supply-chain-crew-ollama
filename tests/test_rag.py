import os
import pytest
from tools import rag_tool as _rt
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


@pytest.fixture(autouse=True)
def fresh_session():
    _rt.reset_rag_session()
    yield


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

import re
from tools import rag_tool as _rt


def test_index_is_built_from_structured_clause_records():
    _rt.build_index(force=True)
    got = _rt._collection.get(include=["documents", "metadatas"])
    expected = sum(1 for _ in open(os.path.join(_rt.KB_DIR, "clauses.jsonl"), encoding="utf-8"))
    assert len(got["ids"]) == len(set(got["ids"])) == expected >= 52
    for d, m in zip(got["documents"], got["metadatas"]):
        assert m["clause"] and m["section"] and m["page"] in (1, 2)
        if m["clause"][0].isdigit() or m["clause"].startswith(("VC-", "B.")):
            assert m["clause"] in d, (m["clause"], d[:60])        # the clause number is always inside its own text


def test_clause_header_names_the_clause():
    out = sop_search.run(query="clause 3.3 RFQ quantity")
    assert out.startswith(f"[{SOP}, page 1] clause 3.3\n") and "rounded up to the nearest multiple" in out


def test_clause_that_introduces_a_table_brings_the_table():
    out = sop_search.run(query="approval matrix section 5")
    assert "clause 5.1" in out and "Chief Financial Officer" in out


def test_new_lead_time_clause_is_retrievable():
    assert "clause 4.7" in sop_search.run(query="backup lead time longer than days of cover")


def test_off_topic_query_returns_error_not_noise():
    assert sop_search.run(query="recipe for chocolate cake").startswith("RAG ERROR")


def test_approval_question_leads_with_the_matrix_and_never_returns_contract_text():
    out = sop_search.run(query="Who approves an order above 1 crore?")
    assert out.startswith(f"[{SOP}, page 2] clause Section 5 table") and "Chief Financial Officer" in out
    assert "Vendor_Contracts_and_AVL" not in out


@pytest.mark.parametrize("q,clause", [("what happens if the approver does not respond", "SOP-SC-015"),
                                      ("how long does the CFO have to approve an L3 action", "clause 2.1"),
                                      ("is an RFQ ever released without approval", "clause 1.2"),
                                      ("what happens when nobody decides, is the order held", "SOP-SC-015")])
def test_escalation_document_is_retrievable(q, clause):
    assert clause in sop_search.run(query=q)


def test_whole_page_chunk_does_not_repeat_text_already_returned():
    out = sop_search.run(query="air freight premium for P-3002")
    assert out.count("4.1 Expedited freight") == 1


def test_retrieved_pages_are_tracked_for_citation_checks():
    _rt.RETRIEVED.clear()
    sop_search.run(query="clause 3.3 RFQ quantity")
    assert (SOP, 1) in _rt.RETRIEVED and (SOP, 1, "3.3") in _rt.RETRIEVED_CLAUSES and (SOP, 1, "3") in _rt.RETRIEVED_CLAUSES


def test_repeated_identical_results_carry_a_stop_searching_note_but_still_the_text():
    _rt.reset_rag_session()
    outs = [sop_search.run(query="capacity shortfall clause 4.6") for _ in range(4)]
    assert all(not o.startswith("RAG NOTE") for o in outs[:2])
    assert outs[2].startswith("RAG NOTE: you already retrieved these clauses") and "call 'Trigger n8n Procurement Workflow'" in outs[2]
    assert "clause 4.6" in outs[2] and "monthly capacity is below the RFQ quantity" in outs[2]        # the agent still gets what it asked for
    assert sop_search.run(query="clause 3.3 RFQ quantity").startswith(f"[{SOP}, page 1] clause 3.3")   # other questions are unaffected
    _rt.reset_rag_session()
    assert _rt.RETRIEVED == set() and not sop_search.run(query="capacity shortfall clause 4.6").startswith("RAG NOTE")
