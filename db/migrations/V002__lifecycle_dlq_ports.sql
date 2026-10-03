-- V002: reference ports, action lifecycle state machine, dead-letter queue, pipeline views.
-- Idempotent on purpose: it runs from /docker-entrypoint-initdb.d on a fresh volume AND via tools/migrate.py on
-- an existing database; both paths must converge on the same schema.

-- ---------------------------------------------------------------- ports (referential integrity for lanes)
CREATE TABLE IF NOT EXISTS ports (
    port_name VARCHAR(50) PRIMARY KEY,
    country   VARCHAR(50) NOT NULL,
    kind      VARCHAR(10) NOT NULL CHECK (kind IN ('SEA_PORT', 'INLAND'))
);
INSERT INTO ports (port_name, country, kind) VALUES
    ('Kaohsiung',   'Taiwan',   'SEA_PORT'),
    ('Keelung',     'Taiwan',   'SEA_PORT'),
    ('Yantian',     'China',    'SEA_PORT'),
    ('Penang',      'Malaysia', 'SEA_PORT'),
    ('Cat Lai',     'Vietnam',  'SEA_PORT'),
    ('Nhava Sheva', 'India',    'SEA_PORT'),
    ('Chennai',     'India',    'SEA_PORT'),
    ('Manesar',     'India',    'INLAND')
ON CONFLICT (port_name) DO NOTHING;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_suppliers_export_port') THEN
    ALTER TABLE suppliers ADD CONSTRAINT fk_suppliers_export_port FOREIGN KEY (export_port) REFERENCES ports (port_name);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_po_origin_port') THEN
    ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_origin_port FOREIGN KEY (origin_port) REFERENCES ports (port_name);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_po_dest_port') THEN
    ALTER TABLE purchase_orders ADD CONSTRAINT fk_po_dest_port FOREIGN KEY (dest_port) REFERENCES ports (port_name);
  END IF;
END $$;

-- ---------------------------------------------------------------- action_log: lifecycle state machine
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS status     TEXT NOT NULL DEFAULT 'DELIVERED'
    CHECK (status IN ('PENDING', 'QUEUED', 'DELIVERED', 'REJECTED', 'DEAD'));
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS attempts   INT  NOT NULL DEFAULT 0 CHECK (attempts >= 0);
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE action_log ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE action_log ALTER COLUMN status SET DEFAULT 'PENDING';   -- rows that existed before V002 stay DELIVERED

CREATE OR REPLACE FUNCTION action_log_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION 'action_log is append-only (SOP 6.3: 7-year retention)';
  END IF;
  IF NEW.event_id <> OLD.event_id OR NEW.payload IS DISTINCT FROM OLD.payload
     OR NEW.created_at <> OLD.created_at OR NEW.action_type <> OLD.action_type
     OR NEW.severity <> OLD.severity OR NEW.total_value_inr <> OLD.total_value_inr OR NEW.approver <> OLD.approver THEN
    RAISE EXCEPTION 'action_log record content is immutable; only status, attempts and last_error may change';
  END IF;
  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'PENDING' AND NEW.status IN ('QUEUED', 'DELIVERED', 'REJECTED', 'DEAD'))
    OR (OLD.status = 'QUEUED'  AND NEW.status IN ('DELIVERED', 'DEAD'))) THEN
    RAISE EXCEPTION 'illegal action_log transition % -> %', OLD.status, NEW.status;
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_action_log_update   ON action_log;
DROP TRIGGER IF EXISTS trg_action_log_delete   ON action_log;
DROP TRIGGER IF EXISTS trg_action_log_truncate ON action_log;
CREATE TRIGGER trg_action_log_update   BEFORE UPDATE   ON action_log FOR EACH ROW       EXECUTE FUNCTION action_log_guard();
CREATE TRIGGER trg_action_log_delete   BEFORE DELETE   ON action_log FOR EACH ROW       EXECUTE FUNCTION action_log_guard();
CREATE TRIGGER trg_action_log_truncate BEFORE TRUNCATE ON action_log FOR EACH STATEMENT EXECUTE FUNCTION action_log_guard();

-- ---------------------------------------------------------------- dead-letter queue
CREATE TABLE IF NOT EXISTS dead_letter (
    id            BIGSERIAL PRIMARY KEY,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    source        TEXT NOT NULL CHECK (source IN ('n8n-validate', 'n8n-error', 'tool-delivery', 'tool-validate')),
    event_id      TEXT,
    errors        JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload       JSONB,
    retryable     BOOLEAN NOT NULL DEFAULT FALSE,       -- only delivery failures are worth replaying unchanged
    status        TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RETRYING', 'RESOLVED', 'DEAD')),
    attempts      INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at    TIMESTAMPTZ,
    last_error    TEXT,
    CHECK (NOT retryable OR (event_id IS NOT NULL AND payload IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_dead_letter_queue ON dead_letter (status, next_retry_at) WHERE retryable;
CREATE INDEX IF NOT EXISTS idx_dead_letter_event ON dead_letter (event_id);

-- Concurrency-safe claim: two workers can never receive the same row; rows stuck in RETRYING (crashed worker) are reclaimed.
CREATE OR REPLACE FUNCTION dlq_claim(batch INT DEFAULT 10) RETURNS SETOF dead_letter LANGUAGE sql AS $$
  UPDATE dead_letter d
     SET status = 'RETRYING', attempts = d.attempts + 1, claimed_at = now()
   WHERE d.id IN (SELECT id FROM dead_letter
                   WHERE retryable
                     AND ((status = 'OPEN' AND next_retry_at <= now())
                          OR (status = 'RETRYING' AND claimed_at < now() - interval '10 minutes'))
                   ORDER BY next_retry_at
                   LIMIT batch
                   FOR UPDATE SKIP LOCKED)
  RETURNING d.*;
$$;

-- Outcome of a replay attempt: RESOLVED on success, exponential backoff (2^n minutes, capped at 6 h) otherwise, DEAD after 6 attempts.
CREATE OR REPLACE FUNCTION dlq_release(p_id BIGINT, p_ok BOOLEAN, p_error TEXT DEFAULT NULL) RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE r dead_letter%ROWTYPE;
BEGIN
  SELECT * INTO r FROM dead_letter WHERE id = p_id FOR UPDATE;
  IF NOT FOUND THEN RETURN 'MISSING'; END IF;
  IF p_ok THEN
    UPDATE dead_letter SET status = 'RESOLVED', last_error = NULL WHERE id = p_id;
    RETURN 'RESOLVED';
  ELSIF r.attempts >= 6 THEN
    UPDATE dead_letter SET status = 'DEAD', last_error = left(p_error, 500) WHERE id = p_id;
    UPDATE action_log SET status = 'DEAD', last_error = left(p_error, 500) WHERE event_id = r.event_id AND status = 'QUEUED';
    RETURN 'DEAD';
  ELSE
    UPDATE dead_letter
       SET status = 'OPEN', last_error = left(p_error, 500),
           next_retry_at = now() + (least(power(2, r.attempts), 360) * interval '1 minute')
     WHERE id = p_id;
    RETURN 'OPEN';
  END IF;
END $$;

-- ---------------------------------------------------------------- views
CREATE OR REPLACE VIEW v_part_pipeline AS
SELECT p.part_id,
       COALESCE(sum(po.quantity) FILTER (WHERE po.status IN ('CONFIRMED', 'IN_TRANSIT', 'HOLD-REVIEW')), 0) AS open_po_qty,
       min(po.expected_delivery) FILTER (WHERE po.status IN ('CONFIRMED', 'IN_TRANSIT', 'HOLD-REVIEW'))   AS next_arrival
FROM parts p LEFT JOIN purchase_orders po ON po.part_id = p.part_id
GROUP BY p.part_id;

CREATE OR REPLACE VIEW v_lane_exposure AS
SELECT po.origin_port AS port, pt.country, po.part_id, po.po_id, po.supplier_id, po.status, po.expected_delivery, po.po_value_inr
FROM purchase_orders po JOIN ports pt ON pt.port_name = po.origin_port
WHERE po.status IN ('CONFIRMED', 'IN_TRANSIT', 'HOLD-REVIEW');

-- ---------------------------------------------------------------- grants
GRANT SELECT ON ports, dead_letter, v_part_pipeline, v_lane_exposure TO scm_ro;
GRANT SELECT, INSERT, UPDATE ON action_log TO scm_audit;
GRANT SELECT, INSERT, UPDATE ON dead_letter TO scm_audit;
GRANT USAGE, SELECT ON SEQUENCE dead_letter_id_seq TO scm_audit;
GRANT EXECUTE ON FUNCTION dlq_claim(INT), dlq_release(BIGINT, BOOLEAN, TEXT) TO scm_audit;
REVOKE UPDATE ON dead_letter FROM scm_audit;
GRANT UPDATE (status, attempts, next_retry_at, claimed_at, last_error) ON dead_letter TO scm_audit;
