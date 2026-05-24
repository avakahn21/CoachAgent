from __future__ import annotations
"""Flask application — Twilio WhatsApp webhook + startup initialization."""
import os
from flask import Flask, request, Response, render_template_string
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

import config
import database
import message_router
import plaid_integration
import scheduler
import sheets

app = Flask(__name__)

_setup_sent = set()  # Track numbers that have received setup message


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/reset", methods=["GET"])
def reset():
    """Local dev helper — clears in-memory setup state so the setup flow can re-run."""
    _setup_sent.clear()
    return {"status": "reset", "setup_sent_cleared": True}, 200


@app.route("/test-sheets", methods=["GET"])
def test_sheets():
    from flask import jsonify
    import sheets
    try:
        tasks = sheets.load_checklist()
        sample = tasks[:3]
        return jsonify({
            "status": "ok",
            "total_tasks": len(tasks),
            "sample": [
                {"task": t["Task"], "deadline": t["Deadline"], "completed": t["Completed"]}
                for t in sample
            ],
        })
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/webhook", methods=["POST"])
def webhook():
    print(f"[WEBHOOK HIT] message: {request.form.get('Body', '')[:50]}")
    # TWILIO_VALIDATE_SIGNATURES defaults to True in production (Railway sets it).
    # Set to False in .env for local testing with ngrok.
    _sig_env = os.environ.get("TWILIO_VALIDATE_SIGNATURES", "true")
    validate = _sig_env.lower() == "true"
    print(f"[webhook] TWILIO_VALIDATE_SIGNATURES={_sig_env!r} → validate={validate}")
    if validate:
        validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
        signature = request.headers.get("X-Twilio-Signature", "")
        if not validator.validate(request.url, request.form, signature):
            return Response("Forbidden", status=403)

    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    if not body:
        return _twiml_reply("")

    # Store sender number in DB for scheduled messages
    _store_user_number(from_number)

    # First message setup flow
    if from_number not in _setup_sent:
        _setup_sent.add(from_number)
        is_first = _is_first_message(from_number)
        if is_first:
            reply = message_router.build_setup_message()
            database.save_message("assistant", reply, mode="setup")
            return _twiml_reply(reply)

    # Route message
    reply = message_router.route_message(body)
    return _twiml_reply(reply)


def _twiml_reply(text: str) -> Response:
    resp = MessagingResponse()
    if text:
        resp.message(text)
    return Response(str(resp), mimetype="application/xml")


def _is_first_message(from_number: str) -> bool:
    try:
        history = database.get_recent_history(limit=5)
        return len(history) == 0
    except Exception:
        return True


def _store_user_number(number: str):
    """Persist user phone number for outbound scheduled messages."""
    try:
        database.set_context("user_whatsapp_number", number)
    except Exception:
        pass


_PLAID_LINK_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connect your bank</title>
  <style>
    body { font-family: -apple-system, sans-serif; display: flex; align-items: center;
           justify-content: center; min-height: 100vh; margin: 0; background: #f5f5f5; }
    .card { background: white; padding: 2rem; border-radius: 12px; max-width: 400px;
            text-align: center; box-shadow: 0 2px 16px rgba(0,0,0,0.1); }
    p { color: #555; line-height: 1.5; }
  </style>
</head>
<body>
<div class="card">
  <p id="msg">Opening your bank connection...</p>
</div>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
  var handler = Plaid.create({
    token: "{{ link_token }}",
    onSuccess: function(public_token, metadata) {
      document.getElementById("msg").textContent = "Connecting your account...";
      fetch("/plaid/callback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({public_token: public_token})
      })
      .then(function(r) { return r.json(); })
      .then(function(d) {
        document.getElementById("msg").textContent = d.message || "Connected! You can close this tab.";
      })
      .catch(function() {
        document.getElementById("msg").textContent = "Connection failed. Text \\"connect Plaid\\" to try again.";
      });
    },
    onExit: function(err, metadata) {
      if (err) {
        document.getElementById("msg").textContent = "Something went wrong. Text \\"connect Plaid\\" to try again.";
      } else {
        document.getElementById("msg").textContent = "Setup cancelled. Text \\"connect Plaid\\" to try again.";
      }
    }
  });
  handler.open();
</script>
</body>
</html>"""


@app.route("/plaid/link/<link_token>")
def plaid_link(link_token: str):
    return render_template_string(_PLAID_LINK_HTML, link_token=link_token)


@app.route("/plaid/callback", methods=["POST"])
def plaid_callback():
    from flask import jsonify
    data = request.get_json(force=True) or {}
    public_token = data.get("public_token", "")
    if not public_token:
        return jsonify({"message": "Missing public_token."}), 400

    try:
        access_token, item_id = plaid_integration.exchange_and_store(public_token)
    except Exception as e:
        print(f"Plaid callback error: {e}")
        return jsonify({"message": "Token exchange failed. Text \"connect Plaid\" to try again."}), 500

    # Send WhatsApp confirmation
    user_number = database.get_context("user_whatsapp_number")
    if user_number:
        _send_whatsapp(
            user_number,
            "Your bank account is connected! I'll pull transactions daily and track them "
            "against your budget automatically. Manual logging still works too.\n\n"
            "Sandbox test note: use username *user_good* / password *pass_good* at "
            "First Platypus Bank to add test transactions."
        )

    # Kick off an immediate sync
    try:
        import categorization
        new_count, alerts, needs_clarification = plaid_integration.sync_and_log_transactions(days=7)
        if new_count and user_number:
            _send_whatsapp(user_number, f"Initial sync done — pulled {new_count} recent transactions.")
        for alert in alerts:
            if user_number:
                _send_whatsapp(user_number, alert)
        # Queue clarifications and send the first one
        if needs_clarification and user_number:
            first = needs_clarification[0]
            database.set_current_clarification(first)
            for txn in needs_clarification[1:]:
                database.push_clarification_queue(txn)
            msg = categorization.build_clarification_message(first, first.get("suggested_category", "misc"))
            _send_whatsapp(user_number, msg)
    except Exception as e:
        print(f"Initial Plaid sync error: {e}")

    return jsonify({"message": "Connected! You can close this tab and go back to WhatsApp."})


def _send_whatsapp(to_number: str, body: str):
    from twilio.rest import Client as TwilioClient
    to = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
    TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN).messages.create(
        from_=f"whatsapp:{config.TWILIO_WHATSAPP_NUMBER}",
        to=to,
        body=body,
    )


def _startup():
    """Initialize sheets and start scheduler on first request."""
    try:
        sheets.load_checklist()
        print("Google Sheets connected — checklist loaded.")
    except Exception as e:
        print(f"Warning: Could not load Google Sheets on startup: {e}")

    scheduler.start_scheduler()


@app.before_request
def before_first_request():
    # Run startup once
    if not getattr(app, "_started", False):
        app._started = True
        _startup()


if __name__ == "__main__":
    _startup()
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=os.environ.get("FLASK_ENV") == "development", port=port)
