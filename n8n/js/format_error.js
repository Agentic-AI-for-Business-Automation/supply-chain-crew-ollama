// Error sub-workflow: turn an n8n failure into a dead_letter row payload.
const e = $input.first().json || {};
const msg = e.execution?.error?.message || 'unknown n8n error';
const node = e.execution?.lastNodeExecuted || 'unknown node';
return [{ json: { errors: JSON.stringify([`${e.workflow?.name || 'workflow'} / ${node}: ${msg}`.slice(0, 480)]), execution_url: e.execution?.url || null } }];
