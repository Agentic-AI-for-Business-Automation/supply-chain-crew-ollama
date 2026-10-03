-- V003: approval lifecycle with timeouts and escalation (SOP-SC-015), plus Production Planning notifications (SOP-SC-014 4.5).
-- Idempotent; applied by tools/migrate.py and mounted into /docker-entrypoint-initdb.d for fresh volumes.
-- Time is always an argument (p_now) so every rule can be tested with a controlled clock.

CREATE TABLE IF NOT EXISTS approval_levels (
    rank INT PRIMARY KEY CHECK (rank BETWEEN 1 AND 3),
    name TEXT NOT NULL UNIQUE
);
INSERT INTO approval_levels (rank, name) VALUES
    (1, 'Operations Manager - Procurement'), (2, 'Head of Supply Chain Management'), (3, 'Chief Financial Officer')
ON CONFLICT (rank) DO NOTHING;

CREATE TABLE IF NOT EXISTS approval_policy (
    severity       TEXT PRIMARY KEY CHECK (severity IN ('L2', 'L3')),
    window_minutes INT NOT NULL CHECK (window_minutes > 0),
    reminder_pct   INT NOT NULL CHECK (reminder_pct BETWEEN 1 AND 99)
);
INSERT INTO approval_policy (severity, window_minutes, reminder_pct) VALUES ('L2', 480, 50), ('L3', 240, 50)
ON CONFLICT (severity) DO NOTHING;

CREATE TABLE IF NOT EXISTS approvals (
    event_id         TEXT PRIMARY KEY REFERENCES action_log (event_id),
    required_rank    INT NOT NULL REFERENCES approval_levels (rank),
    current_rank     INT NOT NULL REFERENCES approval_levels (rank),
    status           TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ESCALATED', 'APPROVED', 'REJECTED', 'HELD')),
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    level_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline_at      TIMESTAMPTZ NOT NULL,
    reminded_at      TIMESTAMPTZ,
    escalations      INT NOT NULL DEFAULT 0 CHECK (escalations >= 0),
    decided_by       TEXT,
    decided_at       TIMESTAMPTZ,
    decision_note    TEXT,
    documents        JSONB,
    released_at      TIMESTAMPTZ,
    CHECK (current_rank >= required_rank),
    CHECK (status NOT IN ('APPROVED', 'REJECTED') OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    CHECK (released_at IS NULL OR status = 'APPROVED')
);
CREATE INDEX IF NOT EXISTS idx_approvals_open ON approvals (deadline_at) WHERE status IN ('PENDING', 'ESCALATED');

CREATE TABLE IF NOT EXISTS approval_events (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_id  TEXT NOT NULL REFERENCES action_log (event_id),
    kind      TEXT NOT NULL CHECK (kind IN ('OPENED', 'REMINDER', 'ESCALATED', 'APPROVED', 'REJECTED', 'HELD', 'RELEASED')),
    from_rank INT,
    to_rank   INT,
    actor     TEXT,
    note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_events_event ON approval_events (event_id, id);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id         BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind       TEXT NOT NULL CHECK (kind IN ('APPROVAL_REQUEST', 'REMINDER', 'ESCALATED', 'HELD', 'PRODUCTION', 'DECIDED')),
    event_id   TEXT REFERENCES action_log (event_id),
    recipient  TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CLAIMED', 'LOGGED')),
    attempts   INT NOT NULL DEFAULT 0,
    claimed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_notification_pending ON notification_outbox (id) WHERE status <> 'LOGGED';

-- ---------------------------------------------------------------- guards (state machine, immutability, append-only)
CREATE OR REPLACE FUNCTION approvals_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'approvals rows are never deleted'; END IF;
  IF NEW.event_id <> OLD.event_id OR NEW.opened_at <> OLD.opened_at OR NEW.required_rank <> OLD.required_rank THEN
    RAISE EXCEPTION 'approvals: event_id, opened_at and required_rank are immutable';
  END IF;
  IF OLD.status IN ('APPROVED', 'REJECTED') AND (NEW.status <> OLD.status OR NEW.decided_by IS DISTINCT FROM OLD.decided_by
        OR NEW.decided_at IS DISTINCT FROM OLD.decided_at OR NEW.current_rank <> OLD.current_rank) THEN
    RAISE EXCEPTION 'approvals: % is a terminal state', OLD.status;
  END IF;
  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'PENDING'   AND NEW.status IN ('ESCALATED', 'APPROVED', 'REJECTED', 'HELD'))
    OR (OLD.status = 'ESCALATED' AND NEW.status IN ('APPROVED', 'REJECTED', 'HELD'))
    OR (OLD.status = 'HELD'      AND NEW.status IN ('APPROVED', 'REJECTED'))) THEN
    RAISE EXCEPTION 'illegal approvals transition % -> %', OLD.status, NEW.status;
  END IF;
  IF NEW.documents IS DISTINCT FROM OLD.documents AND OLD.documents IS NOT NULL THEN
    RAISE EXCEPTION 'approvals: generated documents are write-once';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_approvals_guard ON approvals;
CREATE TRIGGER trg_approvals_guard BEFORE UPDATE OR DELETE ON approvals FOR EACH ROW EXECUTE FUNCTION approvals_guard();

CREATE OR REPLACE FUNCTION approval_events_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'approval_events is append-only'; END $$;
DROP TRIGGER IF EXISTS trg_approval_events_guard ON approval_events;
CREATE TRIGGER trg_approval_events_guard BEFORE UPDATE OR DELETE ON approval_events FOR EACH ROW EXECUTE FUNCTION approval_events_guard();

-- ---------------------------------------------------------------- opening an approval (cannot be forgotten by any caller)
CREATE OR REPLACE FUNCTION approval_open() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE req INT; win INT; n JSONB;
BEGIN
  IF NEW.action_type = 'RFQ' THEN
    SELECT rank INTO req FROM approval_levels WHERE name = NEW.approver;
    IF req IS NULL THEN RAISE EXCEPTION 'approver % is not on the approval ladder', NEW.approver; END IF;
    SELECT window_minutes INTO win FROM approval_policy WHERE severity = NEW.severity;
    win := COALESCE(win, (SELECT window_minutes FROM approval_policy WHERE severity = 'L2'));
    INSERT INTO approvals (event_id, required_rank, current_rank, deadline_at)
    VALUES (NEW.event_id, req, req, now() + win * interval '1 minute');
    INSERT INTO approval_events (event_id, kind, to_rank, note) VALUES (NEW.event_id, 'OPENED', req, 'window ' || win || ' min');
    INSERT INTO notification_outbox (kind, event_id, recipient, subject, body)
    VALUES ('APPROVAL_REQUEST', NEW.event_id, NEW.approver, 'Approval needed: ' || NEW.event_id,
            'Action ' || NEW.event_id || ' (' || NEW.severity || ', INR ' || NEW.total_value_inr || ') needs your decision within ' || win || ' minutes.');
  END IF;
  FOR n IN SELECT * FROM jsonb_array_elements(COALESCE(NEW.payload -> 'production_notifications', '[]'::jsonb)) LOOP
    INSERT INTO notification_outbox (kind, event_id, recipient, subject, body)
    VALUES ('PRODUCTION', NEW.event_id, 'Production Planning', 'Stock-out risk: ' || (n ->> 'part_id'),
            'Part ' || (n ->> 'part_id') || ': ' || COALESCE(n ->> 'reason', 'stock-out risk (SOP-SC-014 4.5)') ||
            ' Action ' || NEW.event_id || '. Re-sequence builds toward models not using this part.');
  END LOOP;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_action_log_open_approval ON action_log;
CREATE TRIGGER trg_action_log_open_approval AFTER INSERT ON action_log FOR EACH ROW EXECUTE FUNCTION approval_open();

-- ---------------------------------------------------------------- the clock: reminders, escalation, HOLD (never an automatic approval)
CREATE OR REPLACE FUNCTION approval_tick(p_now TIMESTAMPTZ DEFAULT now())
RETURNS TABLE (t_event_id TEXT, t_action TEXT, t_from_level TEXT, t_to_level TEXT, t_deadline TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r approvals%ROWTYPE; win INT; rp INT; sev TEXT; from_name TEXT; to_name TEXT;
BEGIN
  FOR r IN SELECT * FROM approvals WHERE status IN ('PENDING', 'ESCALATED') ORDER BY opened_at FOR UPDATE SKIP LOCKED LOOP
    SELECT a.severity INTO sev FROM action_log a WHERE a.event_id = r.event_id;
    SELECT p.window_minutes, p.reminder_pct INTO win, rp FROM approval_policy p WHERE p.severity = sev;
    win := COALESCE(win, 480); rp := COALESCE(rp, 50);
    SELECT l.name INTO from_name FROM approval_levels l WHERE l.rank = r.current_rank;
    IF p_now >= r.deadline_at THEN
      IF r.current_rank < 3 THEN
        SELECT l.name INTO to_name FROM approval_levels l WHERE l.rank = r.current_rank + 1;
        UPDATE approvals SET current_rank = r.current_rank + 1, status = 'ESCALATED', level_started_at = p_now,
               deadline_at = p_now + win * interval '1 minute', reminded_at = NULL, escalations = r.escalations + 1
         WHERE approvals.event_id = r.event_id;
        INSERT INTO approval_events (event_id, kind, from_rank, to_rank, note)
        VALUES (r.event_id, 'ESCALATED', r.current_rank, r.current_rank + 1, from_name || ' did not decide in ' || win || ' min');
        INSERT INTO notification_outbox (kind, event_id, recipient, subject, body)
        VALUES ('ESCALATED', r.event_id, to_name, 'Escalated to you: ' || r.event_id,
                from_name || ' did not decide within ' || win || ' minutes. You now have ' || win || ' minutes (SOP-SC-015 2.3).');
        t_event_id := r.event_id; t_action := 'ESCALATED'; t_from_level := from_name; t_to_level := to_name;
        t_deadline := p_now + win * interval '1 minute'; RETURN NEXT;
      ELSE
        UPDATE approvals SET status = 'HELD' WHERE approvals.event_id = r.event_id;
        INSERT INTO approval_events (event_id, kind, from_rank, to_rank, note)
        VALUES (r.event_id, 'HELD', r.current_rank, r.current_rank, 'top level did not decide; nothing is released (SOP-SC-015 2.5)');
        INSERT INTO notification_outbox (kind, event_id, recipient, subject, body)
        VALUES ('HELD', r.event_id, from_name, 'HELD, needs a human decision: ' || r.event_id,
                'No approver decided in time. The RFQs stay held and nothing was sent to suppliers (SOP-SC-015 2.5).');
        t_event_id := r.event_id; t_action := 'HELD'; t_from_level := from_name; t_to_level := from_name; t_deadline := r.deadline_at;
        RETURN NEXT;
      END IF;
    ELSIF r.reminded_at IS NULL AND p_now >= r.level_started_at + (win * rp / 100.0) * interval '1 minute' THEN
      UPDATE approvals SET reminded_at = p_now WHERE approvals.event_id = r.event_id;
      INSERT INTO approval_events (event_id, kind, to_rank, note) VALUES (r.event_id, 'REMINDER', r.current_rank, rp || '% of the window used');
      INSERT INTO notification_outbox (kind, event_id, recipient, subject, body)
      VALUES ('REMINDER', r.event_id, from_name, 'Reminder: ' || r.event_id,
              'Your approval window is ' || rp || '% used; it closes at ' || to_char(r.deadline_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC.');
      t_event_id := r.event_id; t_action := 'REMINDER'; t_from_level := from_name; t_to_level := from_name; t_deadline := r.deadline_at;
      RETURN NEXT;
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------- the decision: authority is never lowered
CREATE OR REPLACE FUNCTION approval_decide(p_event TEXT, p_actor TEXT, p_decision TEXT, p_note TEXT DEFAULT NULL,
                                           p_now TIMESTAMPTZ DEFAULT now()) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r approvals%ROWTYPE; actor_rank INT;
BEGIN
  IF p_decision NOT IN ('APPROVE', 'REJECT') THEN RETURN 'BAD_DECISION'; END IF;
  SELECT rank INTO actor_rank FROM approval_levels WHERE name = p_actor;
  IF actor_rank IS NULL THEN RETURN 'UNKNOWN_ACTOR'; END IF;
  SELECT * INTO r FROM approvals WHERE event_id = p_event FOR UPDATE;
  IF NOT FOUND THEN RETURN 'UNKNOWN_EVENT'; END IF;
  IF r.status IN ('APPROVED', 'REJECTED') THEN RETURN 'ALREADY_DECIDED'; END IF;
  IF actor_rank < r.required_rank THEN RETURN 'DENIED'; END IF;       -- below the SOP-SC-014 section 5 level
  UPDATE approvals SET status = CASE p_decision WHEN 'APPROVE' THEN 'APPROVED' ELSE 'REJECTED' END,
         decided_by = p_actor, decided_at = p_now, decision_note = left(p_note, 500)
   WHERE event_id = p_event;
  INSERT INTO approval_events (event_id, kind, from_rank, to_rank, actor, note)
  VALUES (p_event, CASE p_decision WHEN 'APPROVE' THEN 'APPROVED' ELSE 'REJECTED' END, r.current_rank, actor_rank, p_actor, left(p_note, 500));
  INSERT INTO notification_outbox (kind, event_id, recipient, subject, body)
  VALUES ('DECIDED', p_event, 'Procurement', p_event || ': ' || CASE p_decision WHEN 'APPROVE' THEN 'approved' ELSE 'rejected' END,
          p_actor || ' ' || CASE p_decision WHEN 'APPROVE' THEN 'approved' ELSE 'rejected' END || ' action ' || p_event || '.');
  RETURN CASE p_decision WHEN 'APPROVE' THEN 'APPROVED' ELSE 'REJECTED' END;
END $$;

-- ---------------------------------------------------------------- documents are generated at intake, released only once APPROVED
CREATE OR REPLACE FUNCTION approval_store_documents(p_event TEXT, p_docs JSONB) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE approvals SET documents = p_docs WHERE event_id = p_event AND documents IS NULL;
  RETURN FOUND;
END $$;

CREATE OR REPLACE FUNCTION approval_release(p_event TEXT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r approvals%ROWTYPE;
BEGIN
  SELECT * INTO r FROM approvals WHERE event_id = p_event FOR UPDATE;
  IF NOT FOUND THEN RETURN jsonb_build_object('released', FALSE, 'reason', 'UNKNOWN_EVENT'); END IF;
  IF r.status <> 'APPROVED' THEN RETURN jsonb_build_object('released', FALSE, 'reason', 'NOT_APPROVED', 'status', r.status); END IF;
  IF r.released_at IS NOT NULL THEN RETURN jsonb_build_object('released', FALSE, 'reason', 'ALREADY_RELEASED'); END IF;
  UPDATE approvals SET released_at = now() WHERE event_id = p_event;
  INSERT INTO approval_events (event_id, kind, actor) VALUES (p_event, 'RELEASED', r.decided_by);
  RETURN jsonb_build_object('released', TRUE, 'documents', COALESCE(r.documents, '[]'::jsonb), 'approved_by', r.decided_by);
END $$;

-- ---------------------------------------------------------------- notification dispatch (claim, then mark LOGGED)
CREATE OR REPLACE FUNCTION notify_claim(batch INT DEFAULT 20) RETURNS SETOF notification_outbox
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE notification_outbox n SET status = 'CLAIMED', attempts = n.attempts + 1, claimed_at = now()
   WHERE n.id IN (SELECT id FROM notification_outbox
                   WHERE status = 'PENDING' OR (status = 'CLAIMED' AND claimed_at < now() - interval '10 minutes')
                   ORDER BY id LIMIT batch FOR UPDATE SKIP LOCKED)
  RETURNING n.*;
$$;

CREATE OR REPLACE FUNCTION notify_done(p_ids BIGINT[]) RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE n INT;
BEGIN
  UPDATE notification_outbox SET status = 'LOGGED' WHERE id = ANY(p_ids) AND status = 'CLAIMED';
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- ---------------------------------------------------------------- views and grants
CREATE OR REPLACE VIEW v_approval_status AS
SELECT a.event_id, a.status, rl.name AS required_level, cl.name AS current_level, a.deadline_at, a.escalations,
       a.decided_by, a.decided_at, a.released_at, (a.documents IS NOT NULL) AS documents_held
FROM approvals a JOIN approval_levels rl ON rl.rank = a.required_rank JOIN approval_levels cl ON cl.rank = a.current_rank;

REVOKE ALL ON FUNCTION approval_tick(TIMESTAMPTZ), approval_decide(TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ), approval_store_documents(TEXT, JSONB),
      approval_release(TEXT), notify_claim(INT), notify_done(BIGINT[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION approval_tick(TIMESTAMPTZ), approval_decide(TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ), approval_store_documents(TEXT, JSONB),
      approval_release(TEXT), notify_claim(INT), notify_done(BIGINT[]) TO scm_audit;
GRANT SELECT ON approval_levels, approval_policy, approvals, approval_events, notification_outbox, v_approval_status TO scm_audit, scm_ro;
