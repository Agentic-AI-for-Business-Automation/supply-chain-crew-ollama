// Final answer of the tick workflow (also the HTTP body when it is started through its webhook).
const tick = $('Summarize tick').first().json;
const sent = $('Format notifications').first().json;
const res = $('Collect results').first().json;
const marked = $input.first().json || {};
return [{ json: { ...tick, notifications_dispatched: sent.count, notifications_marked_logged: marked.marked ?? 0,
  emailed: res.emailed, logged_only: res.logged_only, email_failures: res.failed,
  messages: sent.messages.map(m => ({ id: m.id, to: m.to, routed_to_email: !!m.email, text: m.text })),
  delivery: 'email through the SCM Gmail credential where a route exists (notification_routes); always logged in notification_outbox and the n8n execution' } }];
