// Format claimed notifications by kind. Each message carries the routed email address (null when no route is configured).
const rows = $input.all().map(i => i.json).filter(r => r && r.id);
const tag = { APPROVAL_REQUEST: 'APPROVAL NEEDED', REMINDER: 'REMINDER', ESCALATED: 'ESCALATED', HELD: 'HELD - HUMAN DECISION NEEDED', PRODUCTION: 'PRODUCTION PLANNING', DECIDED: 'DECIDED' };
const clean = v => String(v ?? '').replace(/[\r\n\t]+/g, ' ').trim();
const messages = rows.map(r => {
  const label = tag[r.kind] || clean(r.kind);
  const email = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(clean(r.email)) ? clean(r.email) : null;
  return { id: Number(r.id), kind: r.kind, to: clean(r.recipient), email, event_id: r.event_id, attempts: Number(r.attempts || 0),
    subject: `[SCM] ${label}: ${clean(r.subject)}`.slice(0, 200),
    plain: `${clean(r.body)}\n\nEvent: ${clean(r.event_id)}\nRecipient role: ${clean(r.recipient)}\nSent by the Supply Chain Disruption crew (SOP-SC-015).`,
    text: `[${label}] ${clean(r.subject)} - ${clean(r.body)}` };
});
return [{ json: { count: messages.length, ids: messages.map(m => m.id), messages } }];
