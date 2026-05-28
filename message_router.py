from __future__ import annotations
"""Route incoming WhatsApp messages to the right mode and handler."""
import re
from datetime import date
import financial
import checklist
import life_coach
import claude_agent
import database
import sheets
import config

LMU_START = date(2026, 8, 17)

# ── Keyword sets for routing ────────────────────────────────────────────────

FINANCIAL_KEYWORDS = [
    "spent", "paid", "bought", "spending", "budget", "money", "cost",
    "how much", "category", "groceries", "dining", "shopping", "uber",
    "lyft", "gas", "gym", "subscription", "personal care", "erewhon",
    "trader joe", "whole foods", "amazon", "target", "zara", "nobu",
    "$", "dollars", "transaction", "bank", "weekly spend", "monthly spend",
]

CHECKLIST_KEYWORDS = [
    "mark", "complete", "completed", "done", "finished", "push deadline",
    "add task", "remove task", "due this month", "what's still due",
    "whats still due", "checklist", "what have i completed", "what did i complete",
    "deadline", "task",
    # factual query words that route to Python handlers
    "due", "overdue", "urgent", "what's due", "whats due", "what is due",
    "what's next", "whats next", "coming up", "due soon", "this week",
    "my tasks", "list tasks", "show tasks", "status", "progress",
    "how many tasks", "what's left", "whats left",
]

LIFE_COACH_KEYWORDS = [
    "law school", "lmu", "class", "classes", "professor", "exam", "finals",
    "outline", "brief", "torts", "contracts", "civil procedure", "legal writing",
    "property", "constitutional", "1l", "semester", "orientation", "cold call",
    "study", "gym", "cooking", "sleep", "overwhelmed", "stressed", "anxious",
    "falling behind", "update my classes", "add deadline", "exam schedule",
    "new semester", "week ahead", "balance",
]

SETUP_KEYWORDS = [
    "hello", "hi", "hey", "start", "setup", "connect plaid", "get started",
]


def detect_mode(text: str) -> str:
    text_lower = text.lower()

    # Check for explicit context update commands first
    if life_coach.parse_context_update(text) is not None:
        return "life_coach"

    # Score each mode
    financial_score = sum(1 for kw in FINANCIAL_KEYWORDS if kw in text_lower)
    checklist_score = sum(1 for kw in CHECKLIST_KEYWORDS if kw in text_lower)
    life_coach_score = sum(1 for kw in LIFE_COACH_KEYWORDS if kw in text_lower)

    # Spend patterns get a strong financial boost
    if re.search(r"\$[\d,]+", text):
        financial_score += 3
    if re.search(r"(?:spent|paid|bought)\s+\$?[\d,]+", text, re.IGNORECASE):
        financial_score += 5

    if checklist_score == 0 and financial_score == 0 and life_coach_score == 0:
        return "general"

    scores = {"financial": financial_score, "checklist": checklist_score, "life_coach": life_coach_score}
    return max(scores, key=scores.get)


def _handle_connect_plaid(text: str) -> str | None:
    """Return a Plaid Link URL message if the user asked to connect Plaid."""
    if "connect plaid" not in text.lower():
        return None
    import plaid_integration
    if not plaid_integration.PLAID_AVAILABLE:
        return "Plaid isn't configured yet. Add PLAID_CLIENT_ID and PLAID_SECRET to your environment variables."
    if plaid_integration.has_connected_account():
        return (
            "Your bank account is already connected. I pull transactions daily automatically.\n\n"
            "To reconnect a different account, text \"reconnect Plaid\"."
        )
    try:
        link_token = plaid_integration.create_link_token()
        base_url = config.APP_BASE_URL.rstrip("/")
        link_url = f"{base_url}/plaid/link/{link_token}"
        return (
            f"Open this link on your phone to connect your bank account:\n\n{link_url}\n\n"
            f"Once connected, I'll pull and categorize your transactions automatically every day."
        )
    except Exception as e:
        print(f"Plaid link token error: {e}")
        return "Couldn't create the Plaid link right now. Try again in a minute."


def _handle_clarification_reply(text: str) -> str | None:
    """
    If there's a pending clarification and the reply looks like a category,
    log/update the transaction, save the merchant mapping, and advance the queue.
    Returns a reply string or None (meaning: pass through to normal routing).
    """
    import categorization

    current = database.get_current_clarification()
    if not current:
        return None

    category = categorization.parse_category_reply(text)
    if category is None:
        return None  # Not a category reply — let normal routing handle it

    txn = current
    external_id = txn["transaction_id"]
    amount = txn["amount"]
    name = txn["name"]
    normalized = categorization.normalize_merchant(name)

    if category == "ignore":
        database.delete_transaction(external_id)
        msg = f"Got it — ignored the ${amount:.0f} charge from {name}."
    else:
        database.recategorize_transaction(external_id, category)
        database.set_merchant_mapping(normalized, category)
        display = categorization.CATEGORY_DISPLAY.get(category, category)
        budget = config.BUDGET_TARGETS.get(category, 0)
        total = database.get_category_spend_this_month(category)
        pct = (total / budget * 100) if budget else 0
        msg = (
            f"Got it — logged ${amount:.0f} at {name} as {display}. "
            f"Running total: ${total:.0f} / ${budget} ({pct:.0f}%).\n"
            f"I'll remember this merchant as {display} from now on."
        )

    # Clear current and advance queue
    database.set_current_clarification(None)
    nxt = database.pop_clarification_queue()
    if nxt:
        database.set_current_clarification(nxt)
        msg += "\n\n---\n" + categorization.build_clarification_message(
            nxt, nxt.get("suggested_category", "misc")
        )

    return msg


def _handle_correction(text: str) -> str | None:
    """
    Handle 'actually that was X' corrections on the most recently logged Plaid transaction.
    Returns a reply string or None.
    """
    import categorization

    new_category = categorization.parse_correction(text)
    if not new_category:
        return None

    last = database.get_last_plaid_transaction()
    if not last or not last.get("transaction_id"):
        return None

    external_id = last["transaction_id"]
    old_category = last.get("logged_category", "unknown")
    database.recategorize_transaction(external_id, new_category)
    database.set_merchant_mapping(categorization.normalize_merchant(last["name"]), new_category)

    old_display = categorization.CATEGORY_DISPLAY.get(old_category, old_category)
    new_display = categorization.CATEGORY_DISPLAY.get(new_category, new_category)
    return (
        f"Updated — moved ${last['amount']:.0f} at {last['name']} "
        f"from {old_display} to {new_display}. Merchant memory updated."
    )


_ACCOUNTS_PHRASES = [
    "my accounts", "what accounts are connected", "connected accounts",
    "which accounts", "what banks", "show accounts",
]


def _handle_accounts_query(text: str) -> str | None:
    if not any(p in text.lower() for p in _ACCOUNTS_PHRASES):
        return None
    import plaid_integration
    if not plaid_integration.PLAID_AVAILABLE:
        return "Plaid isn't configured — add PLAID_CLIENT_ID and PLAID_SECRET to get started."
    try:
        institutions = plaid_integration.get_connected_accounts()
        return plaid_integration.format_connected_accounts(institutions)
    except Exception as e:
        print(f"Accounts query error: {e}")
        return "Couldn't fetch account info right now. Try again in a moment."


_REPORT_PHRASES = [
    "send my report", "monthly report", "how did i do this month",
    "spending report", "send report", "get my report", "show my report",
]


def _handle_report_request(text: str) -> str | None:
    """Trigger async PDF report if the user asks for their monthly spending report."""
    text_lower = text.lower()
    if not any(p in text_lower for p in _REPORT_PHRASES):
        return None
    import report
    from datetime import date
    today = date.today()
    # Default to current month; if it's the 1st–3rd, report last month
    if today.day <= 3:
        year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    else:
        year, month = today.year, today.month
    return report.trigger_report_async(year, month)


_COACHING_SIGNALS = [
    "stressed", "stress", "worried", "worry", "overwhelmed", "anxious", "anxiety",
    "nervous", "scared", "excited", "help me think", "what should i", "should i",
    "how do i", "advice", "feel", "feeling", "don't know", "not sure",
    "thinking about", "considering", "deciding", "decision",
]

_DATA_SIGNALS = [
    "due", "overdue", "urgent", "deadline", "task", "checklist", "mark",
    "complete", "done", "push", "add", "remove", "list", "show", "status",
    "progress", "how many", "what's left", "whats left", "next",
]


def _is_checklist_coaching(text: str) -> bool:
    """True if the message looks like a coaching/advice question rather than a data query."""
    t = text.lower()
    has_coaching = any(kw in t for kw in _COACHING_SIGNALS)
    has_data = any(kw in t for kw in _DATA_SIGNALS)
    # Coaching if it has coaching signals and no strong data signals
    return has_coaching and not has_data


def _checklist_fallback() -> str:
    """Default response for checklist messages that don't match any handler."""
    try:
        upcoming = sheets.get_upcoming_tasks(days=14)
        if upcoming:
            top = upcoming[0]
            return f"Most urgent: {top['Task']} — due {top['Deadline']}. Text \"what's due\" for the full list."
    except Exception:
        pass
    return "Text \"what's due\" to see your task list, or \"status\" for overall progress."


def route_message(text: str) -> str:
    """Main routing function — returns the reply string."""
    print(f"[ROUTER] routing message to mode")
    database.save_message("user", text, mode="incoming")

    # Plaid connection request — intercept before mode detection
    plaid_response = _handle_connect_plaid(text)
    if plaid_response:
        database.save_message("assistant", plaid_response, mode="financial")
        return plaid_response

    # Accounts query — intercept before mode detection
    accounts_response = _handle_accounts_query(text)
    if accounts_response:
        database.save_message("assistant", accounts_response, mode="financial")
        return accounts_response

    # Monthly PDF report request — intercept before mode detection
    report_response = _handle_report_request(text)
    if report_response:
        database.save_message("assistant", report_response, mode="financial")
        return report_response

    # Clarification reply — intercept before mode detection
    clarification_reply = _handle_clarification_reply(text)
    if clarification_reply:
        database.save_message("assistant", clarification_reply, mode="financial")
        return clarification_reply

    # Correction ("actually that was X") — intercept before mode detection
    correction_reply = _handle_correction(text)
    if correction_reply:
        database.save_message("assistant", correction_reply, mode="financial")
        return correction_reply

    # Stress detection overrides mode — warmth first
    if life_coach.detect_stress(text):
        response = life_coach.get_stress_response(text)
        database.save_message("assistant", response, mode="life_coach")
        return response

    mode = detect_mode(text)

    # ── Financial mode ──────────────────────────────────────────────────────
    if mode == "financial":
        # Try manual spend parse first
        spend_response = financial.log_manual_spend(text)
        if spend_response:
            # Check for threshold alerts to append
            alerts = financial.check_threshold_alerts()
            if alerts:
                spend_response += "\n\n" + "\n".join(alerts)
            database.save_message("assistant", spend_response, mode="financial")
            return spend_response
        # Otherwise let Claude handle the financial query
        response = claude_agent.call_claude(text, mode="financial")
        database.save_message("assistant", response, mode="financial")
        return response

    # ── Checklist mode ──────────────────────────────────────────────────────
    if mode == "checklist":
        # Python handles all factual queries — Claude only for coaching/open-ended
        result = checklist.try_checklist_command(text)
        if result:
            database.save_message("assistant", result, mode="checklist")
            return result
        # Only call Claude if the message looks like coaching, not a data query
        if _is_checklist_coaching(text):
            response = claude_agent.call_claude(text, mode="checklist")
            database.save_message("assistant", response, mode="checklist")
            return response
        # Unmatched factual query — give a helpful default
        result = _checklist_fallback()
        database.save_message("assistant", result, mode="checklist")
        return result

    # ── Life coach mode ─────────────────────────────────────────────────────
    if mode == "life_coach":
        # Try context update first
        ctx_update = life_coach.parse_context_update(text)
        if ctx_update:
            database.save_message("assistant", ctx_update, mode="life_coach")
            return ctx_update
        response = claude_agent.call_claude(text, mode="life_coach")
        database.save_message("assistant", response, mode="life_coach")
        return response

    # ── General fallback ────────────────────────────────────────────────────
    response = claude_agent.call_claude(text, mode="general")
    database.save_message("assistant", response, mode="general")
    return response


def build_setup_message() -> str:
    """First-time setup message with all three modes introduced."""
    try:
        upcoming = checklist.build_monday_digest()
    except Exception:
        upcoming = "(Could not load checklist — check Google Sheets connection.)"

    days_until_lmu = (LMU_START - date.today()).days

    return (
        f"Hey Ava! I'm your personal accountability agent. Here's what I do:\n\n"
        f"💰 *Financial coach* — I track your spending, flag when you're over budget, and give you concrete challenges to hit your 1L budget targets.\n\n"
        f"✅ *Pre-law checklist* — I manage your task list, track deadlines, and surface what needs attention each week.\n\n"
        f"📚 *Law school life coach* — Starting August 17, I'll guide you through 1L at LMU with weekly check-ins, milestone alerts, and wellness accountability.\n\n"
        f"---\n"
        f"LMU start date confirmed: *August 17, 2026* ({days_until_lmu} days away).\n\n"
        f"📊 Google Sheets connected — checklist loaded.\n\n"
        f"🏦 *Ready to connect Plaid?* Reply \"connect Plaid\" to link your bank/cards, or \"manual\" to start logging spend by text.\n\n"
        f"---\n"
        f"🚨 *Your most urgent checklist items:*\n\n{upcoming}"
    )
