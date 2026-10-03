// Error workflow: build the alert email. {skip:true} when no 'SCM Alerts' route is configured.
const err = $('Format error').first().json;
const route = $input.first().json || {};
const email = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(route.email || '')) ? route.email : null;
if (!email) return [{ json: { skip: true } }];
const msg = JSON.parse(err.errors)[0];
return [{ json: { skip: false, email, subject: '[SCM] n8n workflow failed', plain: `${msg}\n\nThe failure was recorded in dead_letter.\nExecution: ${err.execution_url || 'n/a'}` } }];
