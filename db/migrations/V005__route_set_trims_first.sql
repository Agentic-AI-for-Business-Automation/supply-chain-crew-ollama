-- V005: notification_route_set must trim and lower-case the address BEFORE validating it (V004 validated the raw text,
-- so an address with surrounding spaces was rejected). Applied migrations are never edited, hence a new version.
CREATE OR REPLACE FUNCTION notification_route_set(p_recipient TEXT, p_email TEXT) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE clean TEXT := lower(btrim(COALESCE(p_email, '')));
BEGIN
  IF p_recipient NOT IN (SELECT name FROM approval_levels) AND p_recipient NOT IN ('Production Planning', 'Procurement', 'SCM Alerts') THEN
    RETURN 'UNKNOWN_RECIPIENT';
  END IF;
  IF clean !~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' THEN RETURN 'BAD_EMAIL'; END IF;
  INSERT INTO notification_routes (recipient, email) VALUES (p_recipient, clean)
  ON CONFLICT (recipient) DO UPDATE SET email = EXCLUDED.email, updated_at = now();
  RETURN 'OK';
END $$;
REVOKE ALL ON FUNCTION notification_route_set(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION notification_route_set(TEXT, TEXT) TO scm_audit;
