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
    supplier_id       VARCHAR(10) REFERENCES suppliers(supplier_id),
    part_id           VARCHAR(10) REFERENCES parts(part_id),
    quantity          INT NOT NULL CHECK (quantity > 0),
    po_value_inr      NUMERIC(14,2) NOT NULL CHECK (po_value_inr > 0),
    order_date        DATE NOT NULL,
    expected_delivery DATE NOT NULL,
    shipping_route    VARCHAR(100),
    status            VARCHAR(20) NOT NULL CHECK (status IN ('CONFIRMED','IN_TRANSIT','DELIVERED','HOLD-REVIEW')),      -- CONFIRMED / IN_TRANSIT / DELIVERED (+ HOLD-REVIEW at runtime)
    CHECK (expected_delivery >= order_date)
);
CREATE INDEX idx_po_supplier_status ON purchase_orders(supplier_id, status);
CREATE INDEX idx_sp_part_role ON supplier_parts(part_id, sourcing_role);

-- ---------------------------------------------------------------- seed data
INSERT INTO suppliers VALUES
('S101','Formosa Microchip Co.',        'Taiwan',   'Kaohsiung',   'Kaohsiung',        42, 0.94, 'sales@formosa-microchip.example',  'APPROVED'),
('S102','Keelung Precision PCB Ltd.',   'Taiwan',   'Keelung',     'Keelung',          35, 0.91, 'orders@keelung-pcb.example',       'APPROVED'),
('S103','Hsinchu SensorTech Inc.',      'Taiwan',   'Hsinchu',     'Kaohsiung',        38, 0.89, 'b2b@hsinchu-sensortech.example',   'APPROVED'),
('S201','Penang Semicon Sdn. Bhd.',     'Malaysia', 'Penang',      'Penang',           21, 0.90, 'rfq@penang-semicon.example',       'APPROVED'),
('S202','Saigon Circuit Works JSC',     'Vietnam',  'Ho Chi Minh', 'Cat Lai',          24, 0.86, 'sales@saigon-circuit.example',     'APPROVED'),
('S301','Bengaluru Embedded Systems',   'India',    'Bengaluru',   'Domestic (road)',  14, 0.88, 'rfq@blr-embedded.example',         'APPROVED'),
('S302','Chennai Interconnect Ltd.',    'India',    'Chennai',     'Domestic (road)',  10, 0.95, 'orders@chennai-interconnect.example','APPROVED'),
('S401','Shenzhen PowerCell Co.',       'China',    'Shenzhen',    'Yantian',          30, 0.92, 'export@sz-powercell.example',      'APPROVED'),
('S402','Pune CellWorks Pvt. Ltd.',     'India',    'Pune',        'Domestic (road)',  20, 0.84, 'supply@pune-cellworks.example',    'CONDITIONAL');

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

INSERT INTO purchase_orders VALUES
('PO-26-0412','S101','P-1001', 36000, 15120000.00, CURRENT_DATE - 30, CURRENT_DATE + 12, 'Kaohsiung -> Nhava Sheva (sea)', 'IN_TRANSIT'),
('PO-26-0418','S101','P-1002', 60000, 36600000.00, CURRENT_DATE - 20, CURRENT_DATE + 22, 'Kaohsiung -> Nhava Sheva (sea)', 'CONFIRMED'),
('PO-26-0421','S102','P-2001', 30000, 26400000.00, CURRENT_DATE - 18, CURRENT_DATE + 17, 'Keelung -> Nhava Sheva (sea)',   'CONFIRMED'),
('PO-26-0425','S103','P-3002', 24000, 16560000.00, CURRENT_DATE - 25, CURRENT_DATE + 13, 'Kaohsiung -> Nhava Sheva (sea)', 'IN_TRANSIT'),
('PO-26-0430','S103','P-3001', 40000,  6000000.00, CURRENT_DATE - 10, CURRENT_DATE + 28, 'Kaohsiung -> Nhava Sheva (sea)', 'CONFIRMED'),
('PO-26-0433','S401','P-5001',2500000,662500000.00,CURRENT_DATE - 15, CURRENT_DATE + 15, 'Yantian -> Chennai (sea)',       'IN_TRANSIT'),
('PO-26-0437','S302','P-4001', 200000,  7600000.00, CURRENT_DATE - 3,  CURRENT_DATE + 7,  'Chennai -> Manesar (road)',      'CONFIRMED');

-- Convenience view the Analyst can use (it may also write its own joins)
CREATE VIEW v_part_risk AS
SELECT p.part_id, p.part_name, p.is_critical,
       i.on_hand_qty, i.safety_stock_qty, i.daily_consumption,
       ROUND(i.on_hand_qty::NUMERIC / NULLIF(i.daily_consumption,0), 1) AS days_of_cover,
       s.supplier_id AS primary_supplier_id, s.supplier_name AS primary_supplier,
       s.country AS primary_country, s.export_port AS primary_port
FROM parts p
JOIN inventory i       ON i.part_id = p.part_id
JOIN supplier_parts sp ON sp.part_id = p.part_id AND sp.sourcing_role = 'PRIMARY'
JOIN suppliers s       ON s.supplier_id = sp.supplier_id;
COMMENT ON VIEW v_part_risk IS 'Days of cover per part; Analyst may use it or write own joins';
-- Data-quality guardrails: fail fast if seed drifts from the A5 contract
DO $$ BEGIN
  IF (SELECT COUNT(*) FROM parts) <> 7 THEN RAISE EXCEPTION 'parts must be 7 rows'; END IF;
  IF (SELECT COUNT(*) FROM suppliers) <> 9 THEN RAISE EXCEPTION 'suppliers must be 9 rows'; END IF;
END $$;
