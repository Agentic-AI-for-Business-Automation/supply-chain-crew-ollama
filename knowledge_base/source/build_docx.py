"""Build editable .docx originals mirroring the PDFs.
Run: python knowledge_base/source/build_docx.py
Requires: pip install python-docx
The .docx files are the editable source; PDFs are rebuilt via build_pdfs.py.
Page break is inserted exactly where [PAGE BREAK] is marked in the spec.
"""
import html, os, sys
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_BREAK

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_pdfs import escalation_flow, sop_flow, vendor_flow   # single source of text for PDF and DOCX

FONT = "Calibri"


def style_doc(doc):
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.font.size = Pt(10.5)
    for hname, sz in [("Heading 1", 14), ("Heading 2", 11), ("Heading 3", 10.5)]:
        hs = doc.styles[hname]
        hs.font.name = FONT
        hs.font.size = Pt(sz)


def add_table(doc, header, rows):
    t = doc.add_table(rows=1 + len(rows), cols=len(header))
    t.style = "Light Grid Accent 1"
    for j, h in enumerate(header):
        c = t.cell(0, j)
        c.text = ""
        r = c.paragraphs[0].add_run(h)
        r.bold = True
        r.font.size = Pt(9)
        r.font.name = FONT
    for i, row in enumerate(rows, 1):
        for j, val in enumerate(row):
            c = t.cell(i, j)
            c.text = ""
            r = c.paragraphs[0].add_run(val)
            r.font.size = Pt(9)
            r.font.name = FONT


def render(path, flow):
    doc = Document()
    style_doc(doc)
    for kind, val in flow:
        if kind == "title":
            doc.add_heading(html.unescape(val), level=1)
        elif kind == "subtitle":
            doc.add_heading(html.unescape(val), level=2)
        elif kind == "meta" or kind == "p":
            doc.add_paragraph(html.unescape(val))
        elif kind == "h":
            doc.add_heading(html.unescape(val), level=3)
        elif kind == "table":
            add_table(doc, [html.unescape(c) for c in val[0]], [[html.unescape(c) for c in r] for r in val[1]])
        elif kind == "break":
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.save(path)
    print("wrote", path)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    render(os.path.join(here, "SOP-SC-014_Supply_Disruption_Response.docx"), sop_flow())
    render(os.path.join(here, "Vendor_Contracts_and_AVL.docx"), vendor_flow())
    render(os.path.join(here, "SOP-SC-015_Approval_Timeouts_and_Escalation.docx"), escalation_flow())


if __name__ == "__main__":
    main()
