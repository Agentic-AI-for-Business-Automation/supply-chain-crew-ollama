"""Build both PDFs with exact 2-page breaks and extractable clause numbers.
Run: python knowledge_base/source/build_pdfs.py
Requires: pip install reportlab pypdf
"""
import os
import re
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
        ("meta", "Owner: Head of Supply Chain Management \u00b7 Version: 3.2 \u00b7 Applies to: Manesar plant, all direct-material categories. Effective: 2026-10-01 \u00b7 Supersedes: none."),
        ("h", "Section 1 - Purpose and Scope"),
        ("p", "1.1 This SOP defines the mandatory steps when an external event (port congestion or closure, extreme weather, geopolitical tension, sanctions, supplier force majeure) threatens inbound supply of direct materials."),
        ("p", "1.2 Every automated or manual action taken under this SOP must cite the clause that authorises it."),
        ("h", "Section 2 - Severity Classification"),
        ("table", sev_table),
        ("p", "2.2 Where the delay is uncertain, the analyst must use the upper end of the credible estimate (conservative planning principle)."),
        ("p", "2.3 An event is confirmed only when two independent sources report it. An unconfirmed event is classed L1 and no RFQ is raised."),
        ("h", "Section 3 - RFQ Trigger Rule"),
        ("p", "3.1 For each affected part compute days of cover = on-hand quantity / daily consumption."),
        ("p", "3.2 An RFQ to an approved backup supplier is mandatory when: days of cover &lt; projected delay (days) + 5-day safety buffer."),
        ("p", "3.3 Shortfall units = (projected delay + 5 - days of cover) x daily consumption. RFQ quantity = shortfall units x 1.2, rounded up to the nearest multiple of the backup supplier's MOQ."),
        ("p", "3.4 RFQs may only be issued to suppliers listed as APPROVED_BACKUP for that part in the ERP and marked APPROVED on the Approved Vendor List (Vendor Contracts document, Schedule A). CONDITIONAL suppliers require Head of SCM sign-off before an RFQ is released."),
        ("p", "3.5 For L3 events affecting a critical part, RFQs must go to at least two approved backups where two exist."),
        ("p", "3.6 Parts whose days of cover are equal to or above projected delay + 5 days are placed on part watch status (not an event severity); no RFQ is raised."),
        ("break", ""),
        ("h", "Section 4 - Contingency Measures"),
        ("p", "4.1 Expedited freight: Air freight may be authorised for critical parts when the freight premium is below 15% of the affected PO value. The premium is taken from the carrier quote supplied by the buyer; if no quote is available, record freight quote required and do not estimate."),
        ("p", "4.2 Safety stock uplift: During any active L2 or L3 event the safety stock target for affected parts is raised by 30% until the event is closed."),
        ("p", "4.3 Re-routing: For port-specific events, the buyer shall request the primary supplier to re-route via an alternate export port (e.g. Kaohsiung to Keelung, or trans-shipment via Singapore) before cancelling open POs."),
        ("p", "4.4 Open POs: Open POs with the disrupted supplier are not cancelled; they are placed on HOLD-REVIEW and re-dated."),
        ("p", "4.5 Production protection: If projected stock-out falls within 7 days, Production Planning must be notified to re-sequence builds toward models not using the affected part. Notification is also required when the projected stock-out falls before the earliest backup supplier can deliver (clause 4.7)."),
        ("p", "4.6 Capacity shortfall: If a backup supplier's monthly capacity is below the RFQ quantity, the buyer must combine the RFQ with measures 4.1 or 4.3 for the balance."),
        ("p", "4.7 Lead time check: If a backup supplier's standard lead time is longer than the part's days of cover, the RFQ must be combined with measure 4.1, and the action record must state the expected stock-out gap in days (lead time minus days of cover)."),
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


def escalation_flow():
    """SOP-SC-015: approval timeouts and escalation. Every number comes from schemas/policy_constants.py."""
    import sys
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
    from schemas import policy_constants as pc
    ops, head, cfo = pc.APPROVAL_LADDER
    l2, l3 = pc.APPROVAL_WINDOW_HOURS["L2"], pc.APPROVAL_WINDOW_HOURS["L3"]
    return [
        ("title", "Aravalli Mobility Pvt Ltd"),
        ("subtitle", "SOP-SC-015: Approval Timeouts and Escalation"),
        ("meta", "Owner: Head of Supply Chain Management \u00b7 Version: 1.0 \u00b7 Effective: 2026-10-03 \u00b7 Supersedes: none \u00b7 "
                 "Applies to: every action record under SOP-SC-014 that contains RFQs."),
        ("h", "Section 1 - Scope"),
        ("p", "1.1 This SOP defines how long each approver has to decide an action record that contains RFQs (SOP-SC-014 section 5), "
              "what happens when nobody decides, and who is told."),
        ("p", "1.2 No RFQ document is released to a supplier until an approver at or above the SOP-SC-014 section 5 level has approved the action record."),
        ("h", "Section 2 - Approval Windows and Escalation"),
        ("p", f"2.1 The approval window starts when the action record is accepted. The window is {l2} hours for an L2 action and {l3} hours for an L3 action, "
              "the same as the response windows in SOP-SC-014 section 2."),
        ("p", f"2.2 When {pc.APPROVAL_REMINDER_PCT}% of the window has passed without a decision, the current approver receives one reminder."),
        ("p", f"2.3 When the window ends without a decision, the action record escalates one level up the ladder: {ops}, then {head}, then {cfo}. "
              "The new level receives the full window again."),
        ("p", "2.4 Escalation never lowers authority. An approver at or above the SOP-SC-014 section 5 level for the total value may approve or reject "
              "at any time, including after escalation; an approver below that level may not."),
        ("h", "Section 3 - When Nobody Decides"),
        ("p", f"3.1 If the {cfo} level also ends its window without a decision, the action record is HELD. A held record releases nothing, "
              f"and an alert goes to the {cfo} level."),
        ("p", "3.2 There is no automatic approval and no bypass of an approver, whatever the amount. A held record stays held until an approver "
              "at or above the required level decides."),
        ("p", f"3.3 Every reminder, escalation, hold and decision is recorded with its time and actor, and kept for {pc.RECORD_RETENTION_YEARS} years "
              "under SOP-SC-014 clause 6.3."),
        ("h", "Section 4 - Production Planning"),
        ("p", f"4.1 Production Planning is notified automatically when SOP-SC-014 clause 4.5 applies: projected stock-out within {pc.STOCKOUT_NOTIFY_DAYS} days, "
              "or before the earliest backup supplier can deliver (clause 4.7). No person has to remember to send this notice."),
    ]


_CLAUSE = re.compile(r"^(VC-\d+\.\d+|\d+\.\d+|B\.\d+)\s")
_SECTION = re.compile(r"^(?:Section (\d+)|Clause (VC-\d+)|(Schedule [AB]))\b")


def clause_records(doc_name: str, flow) -> list[dict]:
    """Structured clause records (id, page, section, text) taken from the same flow that renders the PDF,
    so the retrieval index never depends on re-parsing the PDF (which flattens tables)."""
    records, page, heading, section, prev = [], 1, "", "", None
    head_lines = []
    for kind, val in flow:
        if kind in ("title", "subtitle", "meta"):
            head_lines.append(val)
            continue
        if head_lines and kind != "break":
            records.append({"doc": doc_name, "page": page, "clause": "header", "section": "header", "kind": "header",
                            "text": "\n".join(head_lines)})
            head_lines = []
        if kind == "break":
            page += 1
        elif kind == "h":
            heading = val
            m = _SECTION.match(val)
            section = next(g for g in m.groups() if g) if m else val
        elif kind == "p":
            m = _CLAUSE.match(val)
            if m:
                records.append({"doc": doc_name, "page": page, "clause": m.group(1), "section": section, "kind": "clause",
                                "text": f"{heading}\n{val}"})
        elif kind == "table":
            header, rows = val
            body = "\n".join(" | ".join(r) for r in [header] + list(rows))
            anchor = f"\n{prev}" if prev and _CLAUSE.match(prev) else ""
            records.append({"doc": doc_name, "page": page, "clause": f"{heading.split(' - ')[0]} table", "section": section,
                            "kind": "table", "text": f"{heading}{anchor}\n{body}"})
        prev = val if kind == "p" else None
    return records


def main():
    import json
    here = os.path.dirname(os.path.abspath(__file__))
    kb_dir = os.path.normpath(os.path.join(here, ".."))
    sop_name, vc_name = "SOP-SC-014_Supply_Disruption_Response.pdf", "Vendor_Contracts_and_AVL.pdf"
    esc_name = "SOP-SC-015_Approval_Timeouts_and_Escalation.pdf"
    build(os.path.join(kb_dir, sop_name), sop_flow())
    build(os.path.join(kb_dir, vc_name), vendor_flow())
    build(os.path.join(kb_dir, esc_name), escalation_flow())
    recs = clause_records(sop_name, sop_flow()) + clause_records(vc_name, vendor_flow()) + clause_records(esc_name, escalation_flow())
    with open(os.path.join(kb_dir, "clauses.jsonl"), "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("wrote", os.path.join(kb_dir, "clauses.jsonl"), len(recs), "records")


if __name__ == "__main__":
    main()
