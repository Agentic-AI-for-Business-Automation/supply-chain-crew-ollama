// The database already holds a DELIVERED/DEAD/REJECTED row for this event_id: nothing is re-sent.
const v = $('Validate & Build').first().json;
return [{ json: { ok: true, status: 'DUPLICATE', event_id: v.event_id, rfqs_generated: 0, note: 'event_id already processed; nothing re-sent' } }];
