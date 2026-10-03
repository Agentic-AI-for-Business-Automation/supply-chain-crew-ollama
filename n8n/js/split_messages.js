// One item per message that has an email route; a single {skip:true} item when there is nothing to send.
const msgs = ($input.first().json.messages || []).filter(m => m.email);
if (!msgs.length) return [{ json: { skip: true } }];
return msgs.map(m => ({ json: { skip: false, id: m.id, email: m.email, subject: m.subject, plain: m.plain } }));
