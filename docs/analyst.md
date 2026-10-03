# Analyst — Inventory Impact Agent and SQL Tools

## How it works
- `list_erp_tables` / `describe_erp_tables` / `query_erp` wrap LangChain `SQLDatabase` as CrewAI tools.
- The agent lists tables, describes them, then writes its own JOINs across `suppliers`, `parts`, `supplier_parts`, `inventory`, `purchase_orders`, `v_part_risk`.
- Safety guard: only single read-only `SELECT`/`WITH` statements; writes are rejected; output capped at 100 rows / 6000 chars.
- SQL errors are returned with "Fix the query ... and retry" and `[RECOVERY]` is logged; the agent self-corrects.

## Maths check against A5 (typhoon, worst-case delay 21)
gap_days = 21 + 5 - days_of_cover; shortfall = max(0, gap_days) × daily_consumption.

| Part | days_of_cover | gap_days | shortfall_units |
|---|---|---|---|
| P-1001 | 12 | 14 | 16,800 |
| P-1002 | 21 | 5 | 12,000 |
| P-2001 | 18 | 8 | 9,600 |
| P-3002 | 8 | 18 | 21,600 |
| P-3001 | 35 | -9 → not at risk | 0 |

P-5001 (cover 25, Yantian/China) and P-4001 (cover 40, domestic) are excluded: lane filter BEFORE SOP 3.2.

## Sample Analyst output (canonical typhoon run — used by Person 6 to test the Coordinator)
```
## Exposed parts
| part_id | part_name | critical | primary_supplier_id | days_of_cover | daily_consumption | worst_case_delay | gap_days | shortfall_units |
| P-1001 | 32-bit Automotive MCU | true | S101 | 12 | 1200 | 21 | 14 | 16800 |
| P-1002 | Motor Controller Power MOSFET Module | true | S101 | 21 | 2400 | 21 | 5 | 12000 |
| P-2001 | BMS PCB Assembly (6-layer) | true | S102 | 18 | 1200 | 21 | 8 | 9600 |
| P-3002 | 6-axis IMU Sensor Module | true | S103 | 8 | 1200 | 21 | 18 | 21600 |
## Affected open POs
| po_id | supplier_id | part_id | quantity | po_value_inr | expected_delivery | status | shipping_route |
| PO-26-0412 | S101 | P-1001 | 36000 | 15120000.00 | +12d | IN_TRANSIT | Kaohsiung -> Nhava Sheva (sea) |
| PO-26-0418 | S101 | P-1002 | 60000 | 36600000.00 | +22d | CONFIRMED | Kaohsiung -> Nhava Sheva (sea) |
| PO-26-0421 | S102 | P-2001 | 30000 | 26400000.00 | +17d | CONFIRMED | Keelung -> Nhava Sheva (sea) |
| PO-26-0425 | S103 | P-3002 | 24000 | 16560000.00 | +13d | IN_TRANSIT | Kaohsiung -> Nhava Sheva (sea) |
| PO-26-0430 | S103 | P-3001 | 40000 | 6000000.00 | +28d | CONFIRMED | Kaohsiung -> Nhava Sheva (sea) |
## Backup suppliers
| part_id | supplier_id | supplier_name | sourcing_role | avl_status | unit_price_inr | moq | monthly_capacity | standard_lead_days | contact_email |
| P-1001 | S201 | Penang Semicon Sdn. Bhd. | APPROVED_BACKUP | APPROVED | 452.00 | 2000 | 30000 | 21 | rfq@penang-semicon.example |
| P-1001 | S301 | Bengaluru Embedded Systems | APPROVED_BACKUP | APPROVED | 489.00 | 1000 | 12000 | 14 | rfq@blr-embedded.example |
| P-1002 | S201 | Penang Semicon Sdn. Bhd. | APPROVED_BACKUP | APPROVED | 655.00 | 3000 | 45000 | 21 | rfq@penang-semicon.example |
| P-2001 | S202 | Saigon Circuit Works JSC | APPROVED_BACKUP | APPROVED | 930.00 | 1000 | 25000 | 24 | sales@saigon-circuit.example |
| P-3002 | S301 | Bengaluru Embedded Systems | APPROVED_BACKUP | APPROVED | 745.00 | 1000 | 15000 | 14 | rfq@blr-embedded.example |
## Parts not at risk
| P-3001: cover 35 >= 26 (delay 21 + 5 buffer) |
| P-4001: out of scope (domestic Chennai lane), cover 40 |
| P-5001: out of scope (Yantian/China lane), cover 25 |
## SQL used
SELECT ... FROM v_part_risk ... (joins over suppliers/supplier_parts/inventory/purchase_orders)
```

## Part B+ notes
1. **Arithmetic in SQL vs LLM:** LLM multiplication slips on e.g. 16800×1.2; the `query_erp` docstring pushes gap/shortfall into SQL expressions (`(21+5-days_of_cover)*daily_consumption`) so the DB computes exactly. Tested: 5/5 runs matched A5 with SQL arithmetic; LLM arithmetic showed occasional rounding drift.
2. **Lane filter before trigger:** P-5001 (cover 25 < 26) looks triggerable but is Yantian/China — out of scope for a Taiwan event. Chosen pattern: filter by `primary_country IN (...) OR shipping_route LIKE 'Kaohsiung%'/'Keelung%'` FIRST, then apply SOP 3.2 gap test.
3. **Capacity sharing:** S301 serves P-1001/P-3001/P-3002 on per-part capacities 12000/30000/15000. In this demo only P-3002 needs S301 (26000 vs 15000 → breach, flagged for Coordinator §4.6). If P-1001 also fell back to S301, summed demand would exceed any line; we did not hard-code the check — proposed Owner: Analyst vs Coordinator is Coordinator §4.6 per SOP.
