"""Generate the two n8n workflow JSON files from n8n/js/*.js so the JavaScript lives in reviewable files.
Run: python n8n/build_workflows.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
PG = {"postgres": {"id": "scmpostgres00001", "name": "SCM audit DB"}}
GMAIL = {"gmailOAuth2": {"id": "scmgmail000000001", "name": "SCM Gmail"}}
HDR = {"httpHeaderAuth": {"id": "scmwebhooktoken01", "name": "SCM webhook token"}}


def js(name):
    with open(os.path.join(HERE, "js", name), encoding="utf-8") as f:
        return f.read()


def code(i, name, file, pos):
    return {"parameters": {"jsCode": js(file)}, "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name,
            "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos}


def pg(i, name, query, replacement, pos, on_error=None):
    node = {"parameters": {"operation": "executeQuery", "query": query,
                           "options": ({"queryReplacement": replacement} if replacement else {})},
            "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name, "type": "n8n-nodes-base.postgres",
            "typeVersion": 2.5, "position": pos, "credentials": PG, "alwaysOutputData": True}
    if on_error:
        node["onError"] = on_error
    return node


def if_node(i, name, expr, pos):
    return {"parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                                          "conditions": [{"id": f"c0a1b2c3-{i:04d}-4000-8000-000000000001", "leftValue": expr, "rightValue": "",
                                                          "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                                          "combinator": "and"}, "options": {}},
            "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos}


def respond(i, name, pos, code_=None):
    p = {"respondWith": "json", "responseBody": "={{ $json }}", "options": {}}
    if code_:
        p["options"] = {"responseCode": code_}
    return {"parameters": p, "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name,
            "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": pos}


CLAIM_SQL = """WITH ins AS (
  INSERT INTO action_log (event_id, action_type, severity, total_value_inr, approver, payload, status, attempts)
  VALUES ($1, $2, $3, $4::numeric, $5, $6::jsonb, 'DELIVERED', 1)
  ON CONFLICT (event_id) DO NOTHING
  RETURNING event_id, 'NEW'::text AS how),
upd AS (
  UPDATE action_log SET status = 'DELIVERED', attempts = attempts + 1
  WHERE event_id = $1 AND status IN ('PENDING', 'QUEUED') AND NOT EXISTS (SELECT 1 FROM ins)
  RETURNING event_id, 'CLAIMED'::text AS how)
SELECT event_id, how FROM ins UNION ALL SELECT event_id, how FROM upd;"""
CLAIM_ARGS = ("={{ [ $json.event_id, $json.action_type, $json.severity, $json.total_rfq_value_raw, "
              "$json.routed_for_approval_to, $json.payload_json ] }}")
DLQ_SQL = """INSERT INTO dead_letter (source, event_id, errors, payload)
VALUES ('n8n-validate', $1, $2::jsonb, $3::jsonb) RETURNING id;"""
DLQ_ARGS = "={{ [ $json.event_id, JSON.stringify($json.errors), $json.payload_json ] }}"
ERR_SQL = """INSERT INTO dead_letter (source, errors, payload)
VALUES ('n8n-error', $1::jsonb, jsonb_build_object('execution_url', $2::text)) RETURNING id;"""
ERR_ARGS = "={{ [ $json.errors, $json.execution_url ] }}"


def respond_dynamic(i, name, pos):
    """Respond with the status code and body computed by the previous Code node ({http, body})."""
    return {"parameters": {"respondWith": "json", "responseBody": "={{ $json.body }}", "options": {"responseCode": "={{ $json.http }}"}},
            "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name, "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.1, "position": pos}


def webhook(i, name, path, hook_id, pos, response_mode="responseNode"):
    return {"parameters": {"httpMethod": "POST", "path": path, "authentication": "headerAuth", "responseMode": response_mode, "options": {}},
            "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name, "type": "n8n-nodes-base.webhook", "typeVersion": 2,
            "position": pos, "webhookId": hook_id, "credentials": HDR}


def gmail(i, name, pos):
    """Send one email per item. A failure is data (continue), never a crash: the caller decides whether to retry."""
    return {"parameters": {"resource": "message", "operation": "send", "sendTo": "={{ $json.email }}", "subject": "={{ $json.subject }}",
                           "emailType": "text", "message": "={{ $json.plain }}", "options": {"appendAttribution": False}},
            "id": f"a1b2c3d4-{i:04d}-4000-8000-{i:012d}", "name": name, "type": "n8n-nodes-base.gmail", "typeVersion": 2.1,
            "position": pos, "credentials": GMAIL, "onError": "continueRegularOutput", "alwaysOutputData": True}


def link(node):
    return {"node": node, "type": "main", "index": 0}


HOLD_SQL = """SELECT COALESCE(a.status, 'NOT_REQUIRED') AS approval_status, v.current_level AS approval_level,
       v.deadline_at AS approval_deadline,
       CASE WHEN a.event_id IS NULL THEN NULL ELSE approval_store_documents($1, $2::jsonb) END AS documents_stored
FROM (SELECT $1::text AS event_id) e
LEFT JOIN approvals a ON a.event_id = e.event_id
LEFT JOIN v_approval_status v ON v.event_id = e.event_id;"""
HOLD_ARGS = "={{ [ $json.event_id, JSON.stringify($json.rfq_documents) ] }}"
DECIDE_SQL = """WITH d AS (SELECT approval_decide($1, $2, $3, $4) AS outcome)
SELECT d.outcome, CASE WHEN d.outcome = 'APPROVED' THEN approval_release($1) END AS release FROM d;"""
DECIDE_ARGS = "={{ [ $json.event_id, $json.approver_level, $json.decision, $json.note ] }}"
TICK_SQL = "SELECT * FROM approval_tick();"
CLAIM_NOTE_SQL = "SELECT id, kind, event_id, recipient, subject, body, attempts, email FROM notify_claim_routed(50);"
MARK_SQL = "SELECT notify_done(ARRAY(SELECT jsonb_array_elements_text($1::jsonb)::bigint)) AS marked;"
MARK_ARGS = "={{ [ JSON.stringify($json.ids) ] }}"
ROUTE_SQL = "SELECT email FROM notification_routes WHERE recipient = 'SCM Alerts';"


def main_workflow():
    nodes = [
        {"parameters": {"httpMethod": "POST", "path": "supply-chain-rfq", "authentication": "headerAuth",
                        "responseMode": "responseNode", "options": {}},
         "id": "a1b2c3d4-0001-4000-8000-000000000001", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
         "position": [0, 0], "webhookId": "supply-chain-rfq-webhook", "credentials": HDR},
        code(2, "Validate & Build", "validate_build.js", [240, 0]),
        if_node(4, "Payload valid?", "={{ $json.ok }}", [480, 0]),
        pg(5, "Claim event", CLAIM_SQL, CLAIM_ARGS, [720, -120]),
        if_node(6, "Claimed?", "={{ !!$json.how }}", [960, -120]),
        code(7, "Build documents", "build_documents.js", [1200, -220]),
        pg(14, "Hold documents", HOLD_SQL, HOLD_ARGS, [1440, -220]),
        code(15, "Compose response", "compose_response.js", [1680, -220]),
        respond(8, "Respond 200", [1920, -220]),
        code(9, "Duplicate response", "duplicate_response.js", [1200, -20]),
        respond(10, "Respond 200 duplicate", [1440, -20]),
        pg(11, "Dead-letter (invalid)", DLQ_SQL, DLQ_ARGS, [720, 140], on_error="continueRegularOutput"),
        code(12, "Rejected response", "rejected_response.js", [960, 140]),
        respond(13, "Respond 422", [1200, 140], 422),
    ]
    c = lambda a, b, out=0: {"node": b, "type": "main", "index": 0}
    conn = {
        "Webhook": {"main": [[c(0, "Validate & Build")]]},
        "Validate & Build": {"main": [[c(0, "Payload valid?")]]},
        "Payload valid?": {"main": [[c(0, "Claim event")], [c(0, "Dead-letter (invalid)")]]},
        "Claim event": {"main": [[c(0, "Claimed?")]]},
        "Claimed?": {"main": [[c(0, "Build documents")], [c(0, "Duplicate response")]]},
        "Build documents": {"main": [[c(0, "Hold documents")]]},
        "Hold documents": {"main": [[c(0, "Compose response")]]},
        "Compose response": {"main": [[c(0, "Respond 200")]]},
        "Duplicate response": {"main": [[c(0, "Respond 200 duplicate")]]},
        "Dead-letter (invalid)": {"main": [[c(0, "Rejected response")]]},
        "Rejected response": {"main": [[c(0, "Respond 422")]]},
    }
    return {"name": "Supply Chain RFQ & Contingency Handler", "nodes": nodes, "connections": conn, "active": True,
            "settings": {"executionOrder": "v1", "executionTimeout": 60, "saveDataErrorExecution": "all",
                         "saveDataSuccessExecution": "all", "errorWorkflow": "scmerrorhandler001"},
            "id": "scmrfqworkflow0001"}


def error_workflow():
    nodes = [
        {"parameters": {}, "id": "e1000000-0001-4000-8000-000000000001", "name": "Error Trigger",
         "type": "n8n-nodes-base.errorTrigger", "typeVersion": 1, "position": [0, 0]},
        code(21, "Format error", "format_error.js", [240, 0]),
        pg(22, "Record in dead_letter", ERR_SQL, ERR_ARGS, [480, 0], on_error="continueRegularOutput"),
        pg(23, "Look up alert route", ROUTE_SQL, "", [720, 0], on_error="continueRegularOutput"),
        code(24, "Build alert mail", "build_alert_mail.js", [960, 0]),
        if_node(25, "Alert route configured?", "={{ !$json.skip }}", [1200, 0]),
        gmail(26, "Send alert email", [1440, -60]),
    ]
    conn = {"Error Trigger": {"main": [[{"node": "Format error", "type": "main", "index": 0}]]},
            "Format error": {"main": [[{"node": "Record in dead_letter", "type": "main", "index": 0}]]},
            "Record in dead_letter": {"main": [[{"node": "Look up alert route", "type": "main", "index": 0}]]},
            "Look up alert route": {"main": [[{"node": "Build alert mail", "type": "main", "index": 0}]]},
            "Build alert mail": {"main": [[{"node": "Alert route configured?", "type": "main", "index": 0}]]},
            "Alert route configured?": {"main": [[{"node": "Send alert email", "type": "main", "index": 0}], []]}}
    return {"name": "SCM Error Handler", "nodes": nodes, "connections": conn, "active": True,
            "settings": {"executionOrder": "v1", "saveDataSuccessExecution": "all"}, "id": "scmerrorhandler001"}


def approval_workflow():
    nodes = [
        webhook(31, "Webhook", "supply-chain-approval", "supply-chain-approval-webhook", [0, 0]),
        code(32, "Validate decision", "validate_decision.js", [240, 0]),
        if_node(33, "Decision valid?", "={{ $json.ok }}", [480, 0]),
        pg(34, "Decide", DECIDE_SQL, DECIDE_ARGS, [720, -100]),
        code(35, "Shape response", "shape_decision.js", [960, -100]),
        respond_dynamic(36, "Respond with outcome", [1200, -100]),
        pg(37, "Dead-letter (invalid decision)", DLQ_SQL, DLQ_ARGS, [720, 120], on_error="continueRegularOutput"),
        code(38, "Rejected decision", "rejected_decision.js", [960, 120]),
        respond_dynamic(39, "Respond 422", [1200, 120]),
    ]
    conn = {
        "Webhook": {"main": [[link("Validate decision")]]},
        "Validate decision": {"main": [[link("Decision valid?")]]},
        "Decision valid?": {"main": [[link("Decide")], [link("Dead-letter (invalid decision)")]]},
        "Decide": {"main": [[link("Shape response")]]},
        "Shape response": {"main": [[link("Respond with outcome")]]},
        "Dead-letter (invalid decision)": {"main": [[link("Rejected decision")]]},
        "Rejected decision": {"main": [[link("Respond 422")]]},
    }
    return {"name": "SCM Approval Decision", "nodes": nodes, "connections": conn, "active": True,
            "settings": {"executionOrder": "v1", "executionTimeout": 60, "saveDataErrorExecution": "all",
                         "saveDataSuccessExecution": "all", "errorWorkflow": "scmerrorhandler001"},
            "id": "scmapprovaldecide01"}


def tick_workflow():
    schedule = {"parameters": {"rule": {"interval": [{"field": "minutes", "minutesInterval": 5}]}},
                "id": "a1b2c3d4-0041-4000-8000-000000000041", "name": "Every 5 minutes",
                "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, -120]}
    nodes = [
        schedule,
        webhook(42, "Manual tick (token protected)", "supply-chain-tick", "supply-chain-tick-webhook", [0, 120], response_mode="lastNode"),
        pg(43, "Run approval tick", TICK_SQL, "", [260, 0]),
        code(44, "Summarize tick", "summarize_tick.js", [520, 0]),
        pg(45, "Claim notifications", CLAIM_NOTE_SQL, "", [780, 0]),
        code(46, "Format notifications", "format_notifications.js", [1040, 0]),
        code(49, "Split messages", "split_messages.js", [1300, 0]),
        if_node(50, "Anything to email?", "={{ !$json.skip }}", [1540, 0]),
        gmail(51, "Send email", [1780, -100]),
        code(52, "Collect results", "collect_results.js", [2020, 0]),
        pg(47, "Mark notifications logged", MARK_SQL, MARK_ARGS, [2260, 0]),
        code(48, "Report", "report_tick.js", [2500, 0]),
    ]
    conn = {
        "Every 5 minutes": {"main": [[link("Run approval tick")]]},
        "Manual tick (token protected)": {"main": [[link("Run approval tick")]]},
        "Run approval tick": {"main": [[link("Summarize tick")]]},
        "Summarize tick": {"main": [[link("Claim notifications")]]},
        "Claim notifications": {"main": [[link("Format notifications")]]},
        "Format notifications": {"main": [[link("Split messages")]]},
        "Split messages": {"main": [[link("Anything to email?")]]},
        "Anything to email?": {"main": [[link("Send email")], [link("Collect results")]]},
        "Send email": {"main": [[link("Collect results")]]},
        "Collect results": {"main": [[link("Mark notifications logged")]]},
        "Mark notifications logged": {"main": [[link("Report")]]},
    }
    return {"name": "SCM Approval Escalation Tick", "nodes": nodes, "connections": conn, "active": True,
            "settings": {"executionOrder": "v1", "executionTimeout": 60, "saveDataErrorExecution": "all",
                         "saveDataSuccessExecution": "all", "errorWorkflow": "scmerrorhandler001"},
            "id": "scmapprovaltick0001"}


if __name__ == "__main__":
    for name, wf in (("supply_chain_workflow.json", main_workflow()), ("error_handler_workflow.json", error_workflow()),
                     ("approval_decision_workflow.json", approval_workflow()), ("approval_tick_workflow.json", tick_workflow())):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            json.dump(wf, f, indent=2)
        print("wrote", name)
