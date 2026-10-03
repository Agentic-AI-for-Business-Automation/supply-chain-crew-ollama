// Final answer of the intake workflow. RFQ documents are generated now but HELD in the database until the approver decides
// (SOP-SC-015): they are never part of this response, and the caller is told exactly where the approval stands.
const built = $('Build documents').first().json;
const a = $input.first().json || {};
const status = a.approval_status || 'NOT_REQUIRED';
const out = { ...built };
out.rfq_documents_held = Array.isArray(built.rfq_documents) ? built.rfq_documents.length : 0;
delete out.rfq_documents;
out.approval_status = status;
out.approval_level = a.approval_level || null;
out.approval_deadline = a.approval_deadline || null;
out.rfqs_released = status === 'NOT_REQUIRED' && out.rfq_documents_held === 0 ? true : false;
out.next_step = status === 'NOT_REQUIRED' ? 'no approval needed'
  : `awaiting ${a.approval_level || 'approver'} until ${a.approval_deadline || 'the deadline'}; decide via /webhook/supply-chain-approval`;
return [{ json: out }];
