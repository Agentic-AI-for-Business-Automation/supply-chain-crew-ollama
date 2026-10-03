-- Mock ERP for "Aravalli Mobility Pvt Ltd" (EV two-wheeler OEM, Manesar plant)
-- Auto-executed by Postgres on first container start.
-- DATA-QUALITY NOTES (do not remove):
--  * IDs (S101.., P-1001..) are a contract with SOP PDFs and agent prompts. Never rename.
--  * days_of_cover = on_hand_qty / daily_consumption. Values are engineered for the demo:
--      Taiwan lane (Kaohsiung/Keelung) + 21-day worst-case delay -> 4 RFQs, 1 watch, 2 out-of-scope.
--  * P-5001 (cover 25, China/Yantian) and P-4001 (cover 40, domestic) are OUT OF SCOPE for a
--      Taiwan-only disruption even though 25 < 26. Lane filter (country/port) applies BEFORE the
--      SOP 3.2 trigger test. Document this in your docs/erp_infra.md.
--  * S402 is APPROVED_BACKUP role + CONDITIONAL AVL status -> needs Head of SCM sign-off (SOP 3.4).
--  * S301 capacity 15000 < P-3002 RFQ 26000 -> intentional capacity breach for contingency demo.

CREATE TABLE suppliers (
    supplier_id        VARCHAR(10) PRIMARY KEY,
    supplier_name      VARCHAR(100) NOT NULL,
    country            VARCHAR(50)  NOT NULL,
    city               VARCHAR(50),
    export_port        VARCHAR(50),
    standard_lead_days INT          NOT NULL CHECK (standard_lead_days > 0),
    reliability_score  NUMERIC(3,2) NOT NULL CHECK (reliability_score >= 0 AND reliability_score <= 1),   -- 0..1 on-time-in-full score
    contact_email      VARCHAR(100) CHECK (contact_email LIKE '%@%.%'),
    avl_status         VARCHAR(20)  NOT NULL CHECK (avl_status IN ('APPROVED','CONDITIONAL'))
);

CREATE TABLE parts (
    part_id     VARCHAR(10) PRIMARY KEY,
    part_name   VARCHAR(100) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    is_critical BOOLEAN      NOT NULL,
    uom         VARCHAR(10)  NOT NULL DEFAULT 'EA'
);

CREATE TABLE supplier_parts (
    supplier_id        VARCHAR(10) REFERENCES suppliers(supplier_id),
    part_id            VARCHAR(10) REFERENCES parts(part_id),
    sourcing_role      VARCHAR(20) NOT NULL CHECK (sourcing_role IN ('PRIMARY','APPROVED_BACKUP')),    -- PRIMARY / APPROVED_BACKUP
    unit_price_inr     NUMERIC(12,2) NOT NULL CHECK (unit_price_inr > 0),
    moq                INT NOT NULL CHECK (moq > 0),
    monthly_capacity   INT NOT NULL CHECK (monthly_capacity > 0),
    PRIMARY KEY (supplier_id, part_id)
);

CREATE TABLE inventory (
    part_id           VARCHAR(10) PRIMARY KEY REFERENCES parts(part_id),
    warehouse         VARCHAR(30) NOT NULL CHECK (warehouse = 'Manesar-WH1'),
    on_hand_qty       INT NOT NULL CHECK (on_hand_qty >= 0),
    safety_stock_qty  INT NOT NULL CHECK (safety_stock_qty >= 0),
    daily_consumption INT NOT NULL CHECK (daily_consumption > 0),
    last_updated      DATE NOT NULL DEFAULT CURRENT_DATE
);

CREATE TABLE purchase_orders (
    po_id             VARCHAR(12) PRIMARY KEY CHECK (po_id LIKE 'PO-26-%'),
    supplier_id       VARCHAR(10) NOT NULL,
    part_id           VARCHAR(10) NOT NULL,
    quantity          INT NOT NULL CHECK (quantity > 0),
    unit_price_inr    NUMERIC(12,2) NOT NULL CHECK (unit_price_inr > 0),   -- price snapshot at order time
    po_value_inr      NUMERIC(14,2) NOT NULL CHECK (po_value_inr > 0),
    order_date        DATE NOT NULL,
    expected_delivery DATE NOT NULL,
    origin_port       VARCHAR(50) NOT NULL,
    dest_port         VARCHAR(50) NOT NULL,
    transport_mode    VARCHAR(4)  NOT NULL CHECK (transport_mode IN ('SEA','AIR','ROAD')),
    -- derived, never typed by hand, so lane filters (LIKE 'Kaohsiung%') cannot drift
    shipping_route    VARCHAR(100) GENERATED ALWAYS AS (origin_port || ' -> ' || dest_port || ' (' || lower(transport_mode) || ')') STORED,
    status            VARCHAR(20) NOT NULL CHECK (status IN ('CONFIRMED','IN_TRANSIT','DELIVERED','HOLD-REVIEW')),      -- CONFIRMED / IN_TRANSIT / DELIVERED (+ HOLD-REVIEW at runtime)
    CHECK (expected_delivery >= order_date),
    CHECK (abs(po_value_inr - quantity * unit_price_inr) < 1),
    FOREIGN KEY (supplier_id, part_id) REFERENCES supplier_parts (supplier_id, part_id)
);
CREATE INDEX idx_po_supplier_status ON purchase_orders(supplier_id, status);
CREATE INDEX idx_sp_part_role ON supplier_parts(part_id, sourcing_role);
-- D1: exactly one PRIMARY per part (a second would duplicate the part in v_part_risk)
CREATE UNIQUE INDEX uq_one_primary_per_part ON supplier_parts(part_id) WHERE sourcing_role = 'PRIMARY';
-- D7: lookups used by the Analyst's lane filter and watchlist
CREATE INDEX idx_po_part ON purchase_orders(part_id);
CREATE INDEX idx_po_origin ON purchase_orders(origin_port, status);
CREATE INDEX idx_suppliers_lane ON suppliers(country, export_port);

-- ---------------------------------------------------------------- seed data
INSERT INTO suppliers VALUES
('S101','Formosa Microchip Co.',        'Taiwan',   'Kaohsiung',   'Kaohsiung',        42, 0.94, 'sales@formosa-microchip.example',  'APPROVED'),
('S102','Keelung Precision PCB Ltd.',   'Taiwan',   'Keelung',     'Keelung',          35, 0.91, 'orders@keelung-pcb.example',       'APPROVED'),
('S103','Hsinchu SensorTech Inc.',      'Taiwan',   'Hsinchu',     'Kaohsiung',        38, 0.89, 'b2b@hsinchu-sensortech.example',   'APPROVED'),
('S201','Penang Semicon Sdn. Bhd.',     'Malaysia', 'Penang',      'Penang',           21, 0.90, 'rfq@penang-semicon.example',       'APPROVED'),
('S202','Saigon Circuit Works JSC',     'Vietnam',  'Ho Chi Minh', 'Cat Lai',          24, 0.86, 'sales@saigon-circuit.example',     'APPROVED'),
('S301','Bengaluru Embedded Systems',   'India',    'Bengaluru',   NULL,  14, 0.88, 'rfq@blr-embedded.example',         'APPROVED'),
('S302','Chennai Interconnect Ltd.',    'India',    'Chennai',     NULL,  10, 0.95, 'orders@chennai-interconnect.example','APPROVED'),
('S401','Shenzhen PowerCell Co.',       'China',    'Shenzhen',    'Yantian',          30, 0.92, 'export@sz-powercell.example',      'APPROVED'),
('S402','Pune CellWorks Pvt. Ltd.',     'India',    'Pune',        NULL,  20, 0.84, 'supply@pune-cellworks.example',    'CONDITIONAL');

INSERT INTO parts VALUES
('P-1001','32-bit Automotive MCU',              'Semiconductor', TRUE,  'EA'),
('P-1002','Motor Controller Power MOSFET Module','Semiconductor', TRUE,  'EA'),
('P-2001','BMS PCB Assembly (6-layer)',         'PCB',           TRUE,  'EA'),
('P-3001','Hall-Effect Throttle Sensor',        'Sensor',        FALSE, 'EA'),
('P-3002','6-axis IMU Sensor Module',           'Sensor',        TRUE,  'EA'),
('P-4001','IP67 Harness Connector',             'Electrical',    FALSE, 'EA'),
('P-5001','Li-ion 21700 Cell',                  'Battery',       TRUE,  'EA');

INSERT INTO supplier_parts VALUES
('S101','P-1001','PRIMARY',          420.00,  5000, 60000),
('S201','P-1001','APPROVED_BACKUP',  452.00,  2000, 30000),
('S301','P-1001','APPROVED_BACKUP',  489.00,  1000, 12000),
('S101','P-1002','PRIMARY',          610.00,  5000, 90000),
('S201','P-1002','APPROVED_BACKUP',  655.00,  3000, 45000),
('S102','P-2001','PRIMARY',          880.00,  2000, 40000),
('S202','P-2001','APPROVED_BACKUP',  930.00,  1000, 25000),
('S103','P-3001','PRIMARY',          150.00,  5000, 60000),
('S301','P-3001','APPROVED_BACKUP',  171.00,  2000, 30000),
('S103','P-3002','PRIMARY',          690.00,  2000, 40000),
('S301','P-3002','APPROVED_BACKUP',  745.00,  1000, 15000),
('S302','P-4001','PRIMARY',           38.00, 20000, 250000),
('S401','P-5001','PRIMARY',          265.00, 200000, 3500000),
('S402','P-5001','APPROVED_BACKUP',  298.00, 100000, 900000);

-- days of cover = on_hand / daily_consumption
INSERT INTO inventory (part_id, warehouse, on_hand_qty, safety_stock_qty, daily_consumption) VALUES
('P-1001','Manesar-WH1',  14400,   9600,  1200),   -- 12 days
('P-1002','Manesar-WH1',  50400,  19200,  2400),   -- 21 days
('P-2001','Manesar-WH1',  21600,   9600,  1200),   -- 18 days
('P-3001','Manesar-WH1',  42000,   9600,  1200),   -- 35 days
('P-3002','Manesar-WH1',   9600,   9600,  1200),   --  8 days
('P-4001','Manesar-WH1', 240000,  48000,  6000),   -- 40 days
('P-5001','Manesar-WH1',2400000, 768000, 96000);   -- 25 days

INSERT INTO purchase_orders (po_id, supplier_id, part_id, quantity, unit_price_inr, po_value_inr, order_date, expected_delivery, origin_port, dest_port, transport_mode, status) VALUES
('PO-26-0412','S101','P-1001', 36000, 420.00, 15120000.00, CURRENT_DATE - 30, CURRENT_DATE + 12, 'Kaohsiung', 'Nhava Sheva', 'SEA',  'IN_TRANSIT'),
('PO-26-0418','S101','P-1002', 60000, 610.00, 36600000.00, CURRENT_DATE - 20, CURRENT_DATE + 22, 'Kaohsiung', 'Nhava Sheva', 'SEA',  'CONFIRMED'),
('PO-26-0421','S102','P-2001', 30000, 880.00, 26400000.00, CURRENT_DATE - 18, CURRENT_DATE + 17, 'Keelung',   'Nhava Sheva', 'SEA',  'CONFIRMED'),
('PO-26-0425','S103','P-3002', 24000, 690.00, 16560000.00, CURRENT_DATE - 25, CURRENT_DATE + 13, 'Kaohsiung', 'Nhava Sheva', 'SEA',  'IN_TRANSIT'),
('PO-26-0430','S103','P-3001', 40000, 150.00,  6000000.00, CURRENT_DATE - 10, CURRENT_DATE + 28, 'Kaohsiung', 'Nhava Sheva', 'SEA',  'CONFIRMED'),
('PO-26-0433','S401','P-5001',2500000,265.00,662500000.00, CURRENT_DATE - 15, CURRENT_DATE + 15, 'Yantian',   'Chennai',     'SEA',  'IN_TRANSIT'),
('PO-26-0437','S302','P-4001', 200000, 38.00,  7600000.00, CURRENT_DATE - 3,  CURRENT_DATE + 7,  'Chennai',   'Manesar',     'ROAD', 'CONFIRMED');

-- Convenience view the Analyst can use (it may also write its own joins).
-- LEFT JOINs: a part without a PRIMARY shows NULL supplier instead of silently disappearing.
-- days_of_cover keeps 4 decimals: decisions must not be taken on a value rounded to 1 dp.
CREATE VIEW v_part_risk AS
SELECT p.part_id, p.part_name, p.is_critical,
       i.on_hand_qty, i.safety_stock_qty, i.daily_consumption,
       ROUND(i.on_hand_qty::NUMERIC / NULLIF(i.daily_consumption,0), 4) AS days_of_cover,
       (CURRENT_DATE - i.last_updated) AS data_age_days,
       s.supplier_id AS primary_supplier_id, s.supplier_name AS primary_supplier,
       s.country AS primary_country, COALESCE(s.export_port, 'Domestic (road)') AS primary_port
FROM parts p
JOIN inventory i            ON i.part_id = p.part_id
LEFT JOIN supplier_parts sp ON sp.part_id = p.part_id AND sp.sourcing_role = 'PRIMARY'
LEFT JOIN suppliers s       ON s.supplier_id = sp.supplier_id;
COMMENT ON VIEW v_part_risk IS 'Days of cover per part; Analyst may use it or write own joins';

-- Role APPROVED_BACKUP does not imply AVL approval (S402 is CONDITIONAL): use this view for RFQ eligibility.
CREATE VIEW v_eligible_backups AS
SELECT sp.part_id, sp.supplier_id, s.supplier_name, s.avl_status, sp.unit_price_inr, sp.moq,
       sp.monthly_capacity, s.standard_lead_days, s.contact_email,
       (s.avl_status = 'APPROVED') AS rfq_allowed_without_signoff
FROM supplier_parts sp JOIN suppliers s USING (supplier_id)
WHERE sp.sourcing_role = 'APPROVED_BACKUP';

-- SOP 6.3: durable action record (7-year retention), written by trigger_n8n before it calls n8n.
CREATE TABLE action_log (
    event_id        TEXT PRIMARY KEY CHECK (event_id ~ '^SCD-[0-9]{8}-[0-9A-F]{6}$'),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    action_type     TEXT NOT NULL CHECK (action_type IN ('RFQ','CONTINGENCY_PLAN','MONITOR_ONLY')),
    severity        TEXT NOT NULL CHECK (severity IN ('L1','L2','L3')),
    total_value_inr NUMERIC(16,2) NOT NULL CHECK (total_value_inr >= 0),
    approver        TEXT NOT NULL,
    payload         JSONB NOT NULL
);

-- Data-quality guardrails: fail fast if seed drifts from the A4/A5 contract
DO $$ BEGIN
  IF (SELECT COUNT(*) FROM parts) <> 7 OR (SELECT COUNT(*) FROM suppliers) <> 9
     OR (SELECT COUNT(*) FROM supplier_parts) <> 14 OR (SELECT COUNT(*) FROM inventory) <> 7
     OR (SELECT COUNT(*) FROM purchase_orders) <> 7 THEN
    RAISE EXCEPTION 'seed row counts drifted from A4';
  END IF;
  IF EXISTS (SELECT 1 FROM parts p WHERE (SELECT COUNT(*) FROM supplier_parts sp
             WHERE sp.part_id = p.part_id AND sp.sourcing_role = 'PRIMARY') <> 1) THEN
    RAISE EXCEPTION 'every part needs exactly one PRIMARY supplier';
  END IF;
END $$;

-- Read-only role for the Analyst agent (defence in depth behind the regex guard in tools/sql_tools.py)
CREATE ROLE scm_ro LOGIN PASSWORD 'scm_ro';
GRANT CONNECT ON DATABASE erp TO scm_ro;
GRANT USAGE ON SCHEMA public TO scm_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO scm_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO scm_ro;
ALTER ROLE scm_ro SET default_transaction_read_only = on;
ALTER ROLE scm_ro SET statement_timeout = '5s';
REVOKE EXECUTE ON FUNCTION pg_sleep(double precision) FROM PUBLIC;

-- Audit writer: may only append to action_log (used by tools/n8n_tool.py)
CREATE ROLE scm_audit LOGIN PASSWORD 'scm_audit';
GRANT CONNECT ON DATABASE erp TO scm_audit;
GRANT USAGE ON SCHEMA public TO scm_audit;
GRANT SELECT, INSERT ON action_log TO scm_audit;
ALTER ROLE scm_audit SET statement_timeout = '5s';
