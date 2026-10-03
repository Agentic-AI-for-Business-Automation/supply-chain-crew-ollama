// Turn the database outcome into an HTTP status and body. Authority and state rules live in approval_decide(); this only maps.
const v = $('Validate decision').first().json;
const row = $input.first().json || {};
const outcome = row.outcome || 'ERROR';
const http = { APPROVED: 200, REJECTED: 200, DENIED: 403, UNKNOWN_EVENT: 404, ALREADY_DECIDED: 409, UNKNOWN_ACTOR: 422, BAD_DECISION: 422 }[outcome] ?? 500;
const body = { ok: http === 200, outcome, event_id: v.event_id, approver_level: v.approver_level };
if (outcome === 'APPROVED') {
  const rel = typeof row.release === 'string' ? JSON.parse(row.release) : (row.release || {});
  body.released = !!rel.released;
  body.approved_by = rel.approved_by || v.approver_level;
  body.rfq_documents = rel.documents || [];
  if (!rel.released) body.release_note = rel.reason || 'nothing to release';
} else if (outcome === 'DENIED') {
  body.message = 'this level is below the approval authority required by SOP-SC-014 section 5';
} else if (outcome === 'ALREADY_DECIDED') {
  body.message = 'a decision was already recorded; decisions are final';
}
return [{ json: { http, body } }];
