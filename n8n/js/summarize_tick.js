// What the escalation clock did on this run (reminders, escalations, holds). The database already wrote the notifications.
const rows = $input.all().map(i => i.json).filter(r => r && r.t_event_id);
const count = k => rows.filter(r => r.t_action === k).length;
return [{ json: { ran_at: new Date().toISOString(), actions: rows.length, reminders: count('REMINDER'),
  escalations: count('ESCALATED'), held: count('HELD'),
  detail: rows.map(r => ({ event_id: r.t_event_id, action: r.t_action, from: r.t_from_level, to: r.t_to_level, deadline: r.t_deadline })) } }];
