// Runs only after the database claim succeeded: returns the validated result without the bulky raw payload.
const v = $('Validate & Build').first().json;
const claim = $input.first().json || {};
const out = { ...v, claim: claim.how || 'NEW' };
delete out.payload_json;
return [{ json: out }];
