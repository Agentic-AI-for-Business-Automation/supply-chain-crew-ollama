"""Build both PDFs with exact 2-page breaks and extractable clause numbers.
Run: python knowledge_base/source/build_pdfs.py
Requires: pip install reportlab pypdf
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.units import mm


def make_styles():
    base = getSampleStyleSheet()
    title = ParagraphStyle("DocTitle", parent=base["Heading1"], fontName="Helvetica-Bold",
                           fontSize=14, leading=16, spaceAfter=2)
    subtitle = ParagraphStyle("DocSub", parent=base["Heading2"], fontName="Helvetica-Bold",
                              fontSize=11, leading=14, spaceAfter=4)
    h = ParagraphStyle("SecHead", parent=base["Heading3"], fontName="Helvetica-Bold",
                       fontSize=10.5, leading=13, spaceBefore=6, spaceAfter=3)
    normal = ParagraphStyle("Body", parent=base["Normal"], fontName="Helvetica",
                            fontSize=9.5, leading=12, spaceAfter=3)
    small = ParagraphStyle("Meta", parent=base["Normal"], fontName="Helvetica",
                           fontSize=9, leading=11, spaceAfter=4)
    cell = ParagraphStyle("Cell", parent=base["Normal"], fontName="Helvetica",
                          fontSize=8.2, leading=10)
    cell_h = ParagraphStyle("CellH", parent=base["Normal"], fontName="Helvetica-Bold",
                            fontSize=8.2, leading=10)
    return {"title": title, "subtitle": subtitle, "h": h, "p": normal,
            "meta": small, "cell": cell, "cell_h": cell_h}


def cell_p(text, style):
    return Paragraph(text, style)


def build(path, flow):
    styles = make_styles()
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=32, bottomMargin=32,
                            leftMargin=40, rightMargin=40,
                            title="Aravalli Mobility - confidential",
                            author="Aravalli Mobility Pvt Ltd")
    story = []
    for kind, text in flow:
        if kind == "title":
            story += [Paragraph(text, styles["title"]), Spacer(1, 2)]
        elif kind == "subtitle":
            story += [Paragraph(text, styles["subtitle"]), Spacer(1, 2)]
        elif kind == "h":
            story += [Paragraph(f"<b>{text}</b>", styles["h"]), Spacer(1, 1)]
        elif kind == "p":
            story += [Paragraph(text, styles["p"]), Spacer(1, 1)]
        elif kind == "meta":
            story += [Paragraph(text, styles["meta"]), Spacer(1, 1)]
        elif kind == "break":
            story += [PageBreak()]
        elif kind == "table":
            header, rows = text
            data = [[cell_p(c, styles["cell_h"]) for c in header]]
            for r in rows:
                data.append([cell_p(c, styles["cell"]) for c in r])
            ncols = len(header)
            # Even column widths across available width (~415pt)
            avail = A4[0] - 80
            col_w = [avail / ncols] * ncols
            t = Table(data, colWidths=col_w, repeatRows=1)
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            story += [t, Spacer(1, 5)]
    doc.build(story)
    print("wrote", path)


def sop_flow():
    sev_table = (
        ["Level", "Definition", "Response window"],
        [
            ["L1 - Watch", "Expected delay under 7 days, or event not yet confirmed by two independent sources.", "Monitor; re-assess within 24 h. No RFQ."],
            ["L2 - Material", "Expected delay of 7 to 21 days on a named port, lane or supplier.", "RFQ trigger test (Section 3) within 8 h."],
            ["L3 - Critical", "Expected delay over 21 days, port closure beyond 21 days, blockade, export ban, or supplier force majeure notice.", "RFQ trigger test within 4 h; contingency plan mandatory."],
        ],
    )
    appr_table = (
        ["Total estimated RFQ value (INR)", "Approving authority"],
        [
            ["Up to 25,00,000", "Operations Manager - Procurement"],
            ["25,00,001 to 1,00,00,000", "Head of Supply Chain Management"],
            ["Above 1,00,00,000", "Chief Financial Officer"],
        ],
    )
    return [
        ("title", "Aravalli Mobility Pvt Ltd"),
        ("subtitle", "SOP-SC-014: Supply Disruption Response"),
        ("meta", "Owner: Head of Supply Chain Management \u00b7 Version: 3.2 \u00b7 Applies to: Manesar plant, all direct-material categories."),
        ("h", "Section 1 - Purpose and Scope"),
        ("p", "1.1 This SOP defines the mandatory steps when an external event (port congestion or closure, extreme weather, geopolitical tension, sanctions, supplier force majeure) threatens inbound supply of direct materials."),
        ("p", "1.2 Every automated or manual action taken under this SOP must cite the clause that authorises it."),
        ("h", "Section 2 - Severity Classification"),
        ("table", sev_table),
        ("p", "2.2 Where the delay is uncertain, the analyst must use the upper end of the credible estimate (conservative planning principle)."),
        ("h", "Section 3 - RFQ Trigger Rule"),
        ("p", "3.1 For each affected part compute days of cover = on-hand quantity / daily consumption."),
        ("p", "3.2 An RFQ to an approved backup supplier is mandatory when: days of cover &lt; projected delay (days) + 5-day safety buffer."),
        ("p", "3.3 Shortfall units = (projected delay + 5 - days of cover) x daily consumption. RFQ quantity = shortfall units x 1.2, rounded up to the nearest multiple of the backup supplier's MOQ."),
        ("p", "3.4 RFQs may only be issued to suppliers listed as APPROVED_BACKUP for that part in the ERP and marked APPROVED on the Approved Vendor List (Vendor Contracts document, Schedule A). CONDITIONAL suppliers require Head of SCM sign-off before an RFQ is released."),
        ("p", "3.5 For L3 events affecting a critical part, RFQs must go to at least two approved backups where two exist."),
        ("p", "3.6 Parts whose days of cover exceed projected delay + 5 days are placed on L1 watch; no RFQ is raised."),
        ("break", ""),
        ("h", "Section 4 - Contingency Measures"),
        ("p", "4.1 Expedited freight: Air freight may be authorised for critical parts when the freight premium is below 15% of the affected PO value."),
        ("p", "4.2 Safety stock uplift: During any active L2 or L3 event the safety stock target for affected parts is raised by 30% until the event is closed."),
        ("p", "4.3 Re-routing: For port-specific events, the buyer shall request the primary supplier to re-route via an alternate export port (e.g. Kaohsiung to Keelung, or trans-shipment via Singapore) before cancelling open POs."),
        ("p", "4.4 Open POs: Open POs with the disrupted supplier are not cancelled; they are placed on HOLD-REVIEW and re-dated."),
        ("p", "4.5 Production protection: If projected stock-out falls within 7 days, Production Planning must be notified to re-sequence builds toward models not using the affected part."),
        ("p", "4.6 Capacity shortfall: If a backup supplier's monthly capacity is below the RFQ quantity, the buyer must combine the RFQ with measures 4.1 or 4.3 for the balance."),
        ("h", "Section 5 - Approval Matrix"),
        ("p", "5.1 Approval is based on the total estimated value of all RFQs raised in one action record."),
        ("table", appr_table),
        ("h", "Section 6 - Communication and Records"),
        ("p", "6.1 The Operations Coordinator must publish an action record (RFQ or contingency plan) to the procurement workflow system within the response window of Section 2."),
        ("p", "6.2 The record must include: severity level, affected parts, days of cover, projected delay, shortfall, supplier chosen, quantity, approving authority and policy citations."),
        ("p", "6.3 Records are retained for 7 years for audit."),
    ]


def vendor_flow():
    avl_table = (
        ["ID", "Supplier", "Country", "Status", "Parts covered"],
        [
            ["S101", "Formosa Microchip Co.", "Taiwan", "APPROVED", "P-1001, P-1002 (primary)"],
            ["S102", "Keelung Precision PCB Ltd.", "Taiwan", "APPROVED", "P-2001 (primary)"],
            ["S103", "Hsinchu SensorTech Inc.", "Taiwan", "APPROVED", "P-3001, P-3002 (primary)"],
            ["S201", "Penang Semicon Sdn. Bhd.", "Malaysia", "APPROVED", "P-1001, P-1002 (backup)"],
            ["S202", "Saigon Circuit Works JSC", "Vietnam", "APPROVED", "P-2001 (backup)"],
            ["S301", "Bengaluru Embedded Systems", "India", "APPROVED", "P-1001, P-3001, P-3002 (backup)"],
            ["S302", "Chennai Interconnect Ltd.", "India", "APPROVED", "P-4001 (primary)"],
            ["S401", "Shenzhen PowerCell Co.", "China", "APPROVED", "P-5001 (primary)"],
            ["S402", "Pune CellWorks Pvt. Ltd.", "India", "CONDITIONAL", "P-5001 (backup)"],
        ],
    )
    return [
        ("title", "Aravalli Mobility Pvt Ltd"),
        ("subtitle", "Vendor Contracts Digest and Approved Vendor List (FY 2026-27)"),
        ("h", "Clause VC-1 - Force Majeure (all Master Supply Agreements)"),
        ("p", "VC-1.1 A supplier affected by a force majeure event (natural disaster, port closure, war, blockade, government action) must notify Aravalli in writing within 48 hours."),
        ("p", "VC-1.2 During force majeure Aravalli may source affected volumes from alternate suppliers without penalty and without breaching any volume commitment."),
        ("p", "VC-1.3 If force majeure exceeds 30 days, Aravalli may terminate affected POs without liability."),
        ("h", "Clause VC-2 - Late Delivery Penalty"),
        ("p", "VC-2.1 For delays not covered by force majeure, the supplier pays liquidated damages of 0.5% of the delayed PO value per week, capped at 5%."),
        ("h", "Clause VC-3 - Primary Supplier Terms"),
        ("p", "VC-3.1 Formosa Microchip Co. (S101), Taiwan: primary for P-1001 and P-1002. Standard lead time 42 days ex-Kaohsiung. Supplier will re-route via Keelung on request at buyer cost, adding 4 days."),
        ("p", "VC-3.2 Keelung Precision PCB Ltd. (S102), Taiwan: primary for P-2001. Lead time 35 days."),
        ("p", "VC-3.3 Hsinchu SensorTech Inc. (S103), Taiwan: primary for P-3001 and P-3002. Ships via Kaohsiung."),
        ("h", "Clause VC-4 - Backup Supplier Terms"),
        ("p", "VC-4.1 Penang Semicon Sdn. Bhd. (S201), Malaysia: pre-qualified backup for P-1001 and P-1002. Price premium up to 8% over primary. Standard lead time 21 days; expedited 10 days at +12% surcharge. Must acknowledge RFQs within 24 h."),
        ("p", "VC-4.2 Saigon Circuit Works JSC (S202), Vietnam: backup for P-2001. Lead time 24 days; expedite 14 days at +10%."),
        ("p", "VC-4.3 Bengaluru Embedded Systems (S301), India: domestic backup for P-1001, P-3001, P-3002. Lead time 14 days; capacity-limited (see ERP monthly_capacity). Preferred for L3 events because it removes sea-freight exposure."),
        ("p", "VC-4.4 Pune CellWorks Pvt. Ltd. (S402), India: CONDITIONAL backup for P-5001 pending PPAP completion; any RFQ requires Head of SCM sign-off (see SOP-SC-014 clause 3.4)."),
        ("break", ""),
        ("h", "Schedule A - Approved Vendor List"),
        ("table", avl_table),
        ("h", "Schedule B - RFQ Standard Terms"),
        ("p", "B.1 Quote validity 15 days."),
        ("p", "B.2 Incoterms: CIF Nhava Sheva for imports, DAP Manesar for domestic."),
        ("p", "B.3 Payment: 60 days from GRN."),
        ("p", "B.4 Quotes must state lead time, MOQ and capacity commitment."),
    ]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    kb_dir = os.path.normpath(os.path.join(here, ".."))
    build(os.path.join(kb_dir, "SOP-SC-014_Supply_Disruption_Response.pdf"), sop_flow())
    build(os.path.join(kb_dir, "Vendor_Contracts_and_AVL.pdf"), vendor_flow())


if __name__ == "__main__":
    main()
