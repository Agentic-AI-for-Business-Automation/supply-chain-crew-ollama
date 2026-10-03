// Validate an approval decision. Never throws: problems become {ok:false} and are routed to a 422 plus a dead_letter row.
const raw = $input.first().json || {};
const p = raw.body ?? raw;
const errors = [];
const APPROVERS = ['Operations Manager - Procurement', 'Head of Supply Chain Management', 'Chief Financial Officer'];
let payloadJson = 'null';
try { payloadJson = JSON.stringify(p) ?? 'null'; } catch (e) { payloadJson = 'null'; }
const candidateId = (p && typeof p === 'object' && typeof p.event_id === 'string') ? p.event_id.slice(0, 60) : null;
try {
  if (!p || typeof p !== 'object' || Array.isArray(p)) throw new Error('body must be a JSON object');
  if (!/^SCD-\d{8}-[0-9A-F]{6}$/.test(p.event_id || '')) errors.push('event_id missing or malformed');
  if (!APPROVERS.includes(p.approver_level)) errors.push(`approver_level must be one of: ${APPROVERS.join(' | ')}`);
  if (!['APPROVE', 'REJECT'].includes(p.decision)) errors.push('decision must be APPROVE or REJECT');
  const note = p.note === undefined || p.note === null ? '' : String(p.note).replace(/[\r\n\t]+/g, ' ').trim();
  if (note.length > 500) errors.push('note is longer than 500 characters');
  if (errors.length) return [{ json: { ok: false, status: 'REJECTED', event_id: candidateId, errors, payload_json: payloadJson } }];
  return [{ json: { ok: true, event_id: p.event_id, approver_level: p.approver_level, decision: p.decision, note, payload_json: payloadJson } }];
} catch (e) {
  return [{ json: { ok: false, status: 'REJECTED', event_id: candidateId, errors: [...errors, `validator crashed: ${e.message}`], payload_json: payloadJson } }];
}
