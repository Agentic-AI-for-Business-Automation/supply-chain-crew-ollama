// Body of the HTTP 422 answer: the validation errors plus the dead_letter row that now holds the payload.
const v = $('Validate & Build').first().json;
const dl = $input.first().json || {};
return [{ json: { ok: false, status: 'REJECTED', event_id: v.event_id, errors: v.errors, dead_letter_id: dl.id ?? null } }];
