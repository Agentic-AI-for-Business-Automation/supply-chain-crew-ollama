-- V004: who receives which notification (email routing), configurable without touching a workflow.
-- No address is seeded: nothing is emailed until a route is set (python -m tools.approvals set-route "<recipient>" <email>).
CREATE TABLE IF NOT EXISTS notification_routes (
    recipient  TEXT PRIMARY KEY,
    email      TEXT NOT NULL CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only recipients the system actually uses can be routed: the approval ladder plus the fixed teams.
CREATE OR REPLACE FUNCTION notification_route_set(p_recipient TEXT, p_email TEXT) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  IF p_recipient NOT IN (SELECT name FROM approval_levels) AND p_recipient NOT IN ('Production Planning', 'Procurement', 'SCM Alerts') THEN
    RETURN 'UNKNOWN_RECIPIENT';
  END IF;
  IF p_email !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' THEN RETURN 'BAD_EMAIL'; END IF;
  INSERT INTO notification_routes (recipient, email) VALUES (p_recipient, lower(trim(p_email)))
  ON CONFLICT (recipient) DO UPDATE SET email = EXCLUDED.email, updated_at = now();
  RETURN 'OK';
END $$;

CREATE OR REPLACE FUNCTION notification_route_clear(p_recipient TEXT) RETURNS INT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE n INT;
BEGIN
  DELETE FROM notification_routes WHERE recipient = p_recipient;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- Claim notifications together with the address they should be emailed to (NULL = no route configured: log only).
CREATE OR REPLACE FUNCTION notify_claim_routed(batch INT DEFAULT 20)
RETURNS TABLE (id BIGINT, kind TEXT, event_id TEXT, recipient TEXT, subject TEXT, body TEXT, attempts INT, email TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT n.id, n.kind, n.event_id, n.recipient, n.subject, n.body, n.attempts, r.email
    FROM notify_claim(batch) n LEFT JOIN notification_routes r ON r.recipient = n.recipient
   ORDER BY n.id;
$$;

REVOKE ALL ON FUNCTION notification_route_set(TEXT, TEXT), notification_route_clear(TEXT), notify_claim_routed(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION notification_route_set(TEXT, TEXT), notification_route_clear(TEXT), notify_claim_routed(INT) TO scm_audit;
GRANT SELECT ON notification_routes TO scm_audit, scm_ro;
