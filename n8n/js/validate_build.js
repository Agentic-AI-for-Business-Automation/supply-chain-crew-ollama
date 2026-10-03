// Validate first, mutate nothing, compute totals and the approver server-side. Never throws: failures become {ok:false}.
const raw = $input.first().json || {};
const p = raw.body ?? raw;
const errors = [];
const APPROVERS = ['Operations Manager - Procurement', 'Head of Supply Chain Management', 'Chief Financial Officer'];
const clean = v => String(v ?? '').replace(/[\r\n\t]+/g, ' ').trim();
const num = (v, name, min, int) => {
  const n = (typeof v === 'string' && v.trim() === '') || v === null || v === undefined ? NaN : Number(v);
  if (!Number.isFinite(n) || n < min || (int && !Number.isInteger(n))) { errors.push(`${name} must be a number >= ${min}${int ? ' (integer)' : ''}, got ${JSON.stringify(v)}`); return 0; }
  return n;
};
let payloadJson = 'null';
try { payloadJson = JSON.stringify(p) ?? 'null'; } catch (e) { payloadJson = 'null'; }
const candidateId = (p && typeof p === 'object' && typeof p.event_id === 'string') ? p.event_id.slice(0, 60) : null;
try {
  if (!p || typeof p !== 'object' || Array.isArray(p)) throw new Error('body must be a JSON object');
  if (!['RFQ', 'CONTINGENCY_PLAN', 'MONITOR_ONLY'].includes(p.action_type)) errors.push(`action_type '${p.action_type}' invalid`);
  if (!/^L[123]$/.test(p.severity || '')) errors.push('severity must be L1|L2|L3');
  if (!/^SCD-\d{8}-[0-9A-F]{6}$/.test(p.event_id || '')) errors.push('event_id missing or malformed');
  const rfqs = Array.isArray(p.rfqs) ? p.rfqs : (p.rfqs === undefined ? [] : (errors.push('rfqs must be an array'), []));
  if (p.action_type === 'RFQ' && rfqs.length === 0) errors.push('RFQ action needs at least one rfqs line');
  if (p.action_type === 'MONITOR_ONLY' && rfqs.length) errors.push('MONITOR_ONLY must have no rfqs');
  const docs = rfqs.map((r, i) => {
    r = r || {};
    const qty = num(r.quantity, `rfqs[${i}].quantity`, 1, true);
    const price = num(r.unit_price_inr, `rfqs[${i}].unit_price_inr`, 0.01, false);
    const days = num(r.required_within_days, `rfqs[${i}].required_within_days`, 1, true);
    if (!/^S\d{3}$/.test(r.supplier_id || '')) errors.push(`rfqs[${i}].supplier_id invalid`);
    if (!/^P-\d{4}$/.test(r.part_id || '')) errors.push(`rfqs[${i}].part_id invalid`);
    const to = clean(r.supplier_email);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) errors.push(`rfqs[${i}].supplier_email invalid or missing`);
    const value = qty * price;
    if (r.est_value_inr !== undefined && Math.abs(Number(r.est_value_inr) - value) > value * 0.01) errors.push(`rfqs[${i}].est_value_inr != quantity x unit_price`);
    return { rfq_number: `${p.event_id}-RFQ${String(i + 1).padStart(2, '0')}`, to,
             subject: clean(`Urgent RFQ ${r.part_id} x ${qty.toLocaleString('en-IN')} - ${clean(p.company)}`),
             body: `Dear ${clean(r.supplier_name)},\n\nPlease quote for ${qty} units of ${clean(r.part_id)} for delivery within ${days} days (CIF Nhava Sheva / DAP Manesar). Quote validity 15 days; state lead time, MOQ and capacity commitment.\n\nReason: ${clean(r.justification)}\n\nProcurement, ${clean(p.company)}`,
             est_value_inr: value };
  });
  const total = docs.reduce((s, d) => s + d.est_value_inr, 0);
  const approver = total <= 2500000 ? 'Operations Manager - Procurement' : total <= 10000000 ? 'Head of Supply Chain Management' : 'Chief Financial Officer';
  if (docs.length && p.approval_authority !== approver) errors.push(`approval_authority must be '${approver}' for total ${total}`);
  if (!docs.length && !APPROVERS.includes(p.approval_authority)) errors.push('approval_authority must be one of the three approvers');
  if (errors.length) return [{ json: { ok: false, status: 'REJECTED', event_id: candidateId, errors, payload_json: payloadJson } }];
  return [{ json: { ok: true, status: 'RECEIVED', event_id: p.event_id, action_type: p.action_type, severity: p.severity,
    routed_for_approval_to: docs.length ? approver : p.approval_authority, rfqs_generated: docs.length,
    total_rfq_value_inr: total.toLocaleString('en-IN'), total_rfq_value_raw: total, rfq_documents: docs,
    contingency_plan: Array.isArray(p.contingency_actions) ? p.contingency_actions.map(clean) : [],
    citations_logged: Array.isArray(p.policy_citations) ? p.policy_citations.length : 0,
    processed_at: new Date().toISOString(), payload_json: payloadJson } }];
} catch (e) {
  return [{ json: { ok: false, status: 'REJECTED', event_id: candidateId, errors: [...errors, `validator crashed: ${e.message}`], payload_json: payloadJson } }];
}
