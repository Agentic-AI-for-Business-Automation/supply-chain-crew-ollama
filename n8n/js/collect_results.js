// Decide which claimed notifications are finished. Sent ok -> finished. No email route -> finished (logged only).
// Send failed -> left CLAIMED so notify_claim re-offers it after 10 minutes, but given up after 5 attempts (never an endless loop).
const MAX_ATTEMPTS = 5;
const msgs = $('Format notifications').first().json.messages || [];
let sent = [];
try { sent = $('Send email').all(); } catch (e) { sent = []; }   // the node did not run: there was nothing to send
const sendable = msgs.filter(m => m.email);
const ok = new Set();
sendable.forEach((m, i) => { const out = (sent[i] || {}).json || {}; if (out.id && !out.error) ok.add(m.id); });
const done = [], failed = [], noRoute = [];
for (const m of msgs) {
  if (!m.email) { noRoute.push(m.id); done.push(m.id); }
  else if (ok.has(m.id)) done.push(m.id);
  else if (m.attempts >= MAX_ATTEMPTS) { failed.push({ id: m.id, gave_up: true }); done.push(m.id); }
  else failed.push({ id: m.id, gave_up: false });
}
return [{ json: { ids: done, emailed: ok.size, logged_only: noRoute.length, failed } }];
