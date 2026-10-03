// Body of the 422 answer for a malformed decision request.
const v = $('Validate decision').first().json;
const dl = $input.first().json || {};
return [{ json: { http: 422, body: { ok: false, status: 'REJECTED', event_id: v.event_id, errors: v.errors, dead_letter_id: dl.id ?? null } } }];
