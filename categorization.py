from __future__ import annotations
"""Smart transaction categorization — confidence scoring, merchant memory, clarification flow."""
import re
from datetime import date as _date
from financial import CATEGORY_KEYWORDS, PLAID_CATEGORY_MAP, categorize

CONFIDENCE_THRESHOLD = 65  # Below this → ask Ava for clarification

_LMU_START = _date(2026, 8, 17)


# ── Fuzzy merchant lookup ───────────────────────────────────────────────────

def _to_words(s: str) -> set[str]:
    """Lowercase, strip non-alphanumeric, return word set for fuzzy matching."""
    return set(re.sub(r"[^a-z0-9]", " ", s.lower()).split())


def lookup_merchant(normalized_name: str, all_mappings: list[dict]) -> dict | None:
    """
    Find the most specific (most words matched) merchant mapping for a normalized name.
    Pattern words must all appear in the target word set.
    """
    target_words = _to_words(normalized_name)
    best: dict | None = None
    best_len = 0
    for m in all_mappings:
        pattern = m.get("merchant_pattern", "")
        if not pattern:
            continue
        pattern_words = _to_words(pattern)
        if pattern_words and pattern_words.issubset(target_words):
            if len(pattern_words) > best_len:
                best = m
                best_len = len(pattern_words)
    return best


def should_flag(merchant_row: dict, amount: float) -> bool:
    """Return True if this transaction should trigger an immediate alert."""
    if merchant_row.get("flag_always"):
        return True
    threshold = merchant_row.get("flag_threshold") or 0
    return bool(threshold and amount >= threshold)


# ── Flag alert builder ──────────────────────────────────────────────────────

def build_flag_alert(merchant_name: str, amount: float, category: str, note: str) -> str:
    """Generate a direct, Ava-specific alert for a flagged transaction."""
    import database
    note_lower = (note or "").lower()
    name_lower = merchant_name.lower()

    if "delivery" in note_lower:
        return f"{merchant_name} — ${amount:.0f}. You said no delivery. What happened?"

    if "erewhon" in name_lower:
        count = database.count_merchant_transactions_this_month("erewhon")
        return f"Erewhon — ${amount:.0f}. That's visit #{count} this month."

    if "equinox" in name_lower:
        days = (_LMU_START - _date.today()).days
        return f"Equinox charged ${amount:.0f} again. {days} days until law school. Cancel this."

    if "should cancel" in note_lower or "cancel" in note_lower:
        return f"{merchant_name} — ${amount:.0f}. {note}."

    if "pet insurance" in note_lower or "wagmo" in name_lower:
        days = (_LMU_START - _date.today()).days
        return f"Wagmo pet insurance — ${amount:.0f}. {days} days until law school. Still keeping this?"

    if "imdb" in name_lower:
        return f"IMDbPro — ${amount:.0f}. Will you use this in law school? Reply 'cancel imdb' or 'keep imdb'."

    if category == "shopping":
        return f"{merchant_name} — ${amount:.0f}. Shopping charge — logged."

    display = CATEGORY_DISPLAY.get(category, category.replace("_", " ").title())
    suffix = f" ({note})" if note else ""
    return f"{merchant_name} — ${amount:.0f} [{display}{suffix}]"


# ── Amount-sensitive merchant logic (Apple, Prime Video) ────────────────────

# Recurring subscription amounts we recognize silently — anything else gets asked
APPLE_SUBSCRIPTION_AMOUNTS: frozenset[float] = frozenset({
    4.99, 6.99, 9.99, 12.99, 13.99, 14.99, 19.99, 32.98,
})
PRIME_SUBSCRIPTION_AMOUNTS: frozenset[float] = frozenset({
    8.99, 14.99, 17.99,
})


def _is_apple_merchant(normalized_name: str) -> bool:
    words = _to_words(normalized_name)
    return "apple" in words and ("com" in words or "bill" in words)


def _is_prime_video_merchant(normalized_name: str) -> bool:
    words = _to_words(normalized_name)
    return "prime" in words and "video" in words


def lookup_amount_specific_merchant(normalized_name: str, amount: float) -> str | None:
    """
    For Apple and Prime Video charges, determine category by amount.
    Returns a category string, "ask" (send clarification), or None (not applicable).
    Checks DB first so user answers are remembered for future charges.
    """
    import database as _db

    if _is_apple_merchant(normalized_name):
        key = f"apple {amount:.2f}"
        saved = _db.get_merchant_mapping(key)
        if saved:
            return saved
        if round(amount, 2) in APPLE_SUBSCRIPTION_AMOUNTS:
            return "subscriptions"
        return "ask"

    if _is_prime_video_merchant(normalized_name):
        key = f"prime {amount:.2f}"
        saved = _db.get_merchant_mapping(key)
        if saved:
            return saved
        if round(amount, 2) in PRIME_SUBSCRIPTION_AMOUNTS:
            return "subscriptions"
        return "ask"

    return None


def get_amount_mapping_key(txn: dict) -> str:
    """Return the merchant_mappings key used to save a user's answer for an amount-specific charge."""
    name = txn.get("name", "")
    amount = txn.get("amount", 0)
    normalized = normalize_merchant(name)
    if _is_apple_merchant(normalized):
        return f"apple {amount:.2f}"
    if _is_prime_video_merchant(normalized):
        return f"prime {amount:.2f}"
    return normalized


# ── Ambiguous payment detection ─────────────────────────────────────────────

_AMBIGUOUS_PATTERNS = [
    (r"apple cash|pmnt sent", "Apple Cash"),
    (r"\bvenmo\b", "Venmo"),
    (r"\bzelle\b", "Zelle"),
    (r"7eleven.fcti|atm withdrawal|atm fee", "ATM"),
]


def detect_ambiguous_payment(merchant_name: str) -> str | None:
    """Return the payment type string if this looks like an ambiguous P2P/cash payment."""
    n = merchant_name.lower()
    for pattern, label in _AMBIGUOUS_PATTERNS:
        if re.search(pattern, n):
            return label
    return None


def build_ambiguous_message(merchant_name: str, amount: float, payment_type: str) -> str:
    """Ask Ava what an unknown P2P payment was for."""
    # Try to extract recipient name for Zelle/Venmo
    recipient = ""
    m = re.search(r"(?:zelle to|venmo to|sent to)\s+([a-z ]+)", merchant_name.lower())
    if m:
        recipient = m.group(1).strip().title()

    if recipient:
        return f"You sent ${amount:.0f} via {payment_type} to {recipient}. What was this for?"
    return f"You sent ${amount:.0f} via {payment_type}. What was this for?"

# ── Merchant name normalization ─────────────────────────────────────────────

# Payment processor prefixes that obscure the real merchant name
_PREFIX_RE = re.compile(
    r"^(?:SQ \*|TST\* |SP \*|PAYPAL \*|VENMO \*|CASH APP \*|"
    r"AMZN MKTP\s*(?:US)?|WWW\.|CHECKCARD |POS |ACH |"
    r"RECURRING |AUTOMATIC PAYMENT |DEBIT |\*)",
    re.IGNORECASE,
)
_TRAILING_CODE_RE = re.compile(r"[\s#*]+\d{4,}\s*$")
_STATE_SUFFIX_RE = re.compile(r"\s+[A-Z]{2}\s*$")


def normalize_merchant(name: str) -> str:
    """Strip POS prefixes, trailing codes, and state suffixes; return lowercase."""
    s = name.strip()
    s = _PREFIX_RE.sub("", s)
    s = _TRAILING_CODE_RE.sub("", s)
    s = _STATE_SUFFIX_RE.sub("", s)
    return s.strip().lower()


def _is_cryptic(original: str, normalized: str) -> bool:
    """Return True when the name is still opaque after normalization."""
    if len(normalized) <= 4:
        return True
    words = normalized.split()
    real_words = [w for w in words if len(w) > 3 and not re.fullmatch(r"[\d*#\-]+", w)]
    return len(real_words) == 0


# ── Amount-based fallback hint ──────────────────────────────────────────────

def _amount_hint(amount: float) -> str:
    if amount < 20:
        return "dining"
    if amount < 60:
        return "misc"
    if amount < 200:
        return "shopping"
    return "personal_care"


# ── Confidence scoring ──────────────────────────────────────────────────────

def score_transaction(
    merchant_name: str,
    plaid_category: str,
    amount: float,
    memory_category: str | None = None,
) -> tuple[str, int]:
    """
    Return (best_category, confidence 0-100).
    Caller should pass memory_category from database.get_merchant_mapping().
    """
    if memory_category:
        return memory_category, 95

    normalized = normalize_merchant(merchant_name)
    cryptic_penalty = 25 if _is_cryptic(merchant_name, normalized) else 0

    # Keyword match: try normalized name first, then original
    name_cat = categorize(normalized)
    if name_cat == "misc":
        name_cat = categorize(merchant_name.lower())
    name_conf = 80 if name_cat != "misc" else 0

    # Plaid category signal
    plaid_cat = PLAID_CATEGORY_MAP.get(plaid_category.upper(), "misc")
    plaid_conf = 70 if plaid_cat != "misc" else 0

    # Pick best source
    if name_cat != "misc":
        best, conf = name_cat, name_conf - cryptic_penalty
    elif plaid_cat != "misc":
        best, conf = plaid_cat, plaid_conf - cryptic_penalty
    else:
        best, conf = _amount_hint(amount), 30 - cryptic_penalty

    # Bonus when name and Plaid agree
    if name_cat != "misc" and plaid_cat == name_cat:
        conf = min(95, conf + 10)

    return best, max(0, int(conf))


# ── Display helpers ─────────────────────────────────────────────────────────

CATEGORY_DISPLAY = {
    "groceries": "Groceries",
    "dining": "Dining",
    "shopping": "Shopping",
    "personal_care": "Personal Care",
    "gas": "Gas",
    "uber_lyft": "Uber/Lyft",
    "entertainment": "Entertainment",
    "gym": "Gym",
    "subscriptions": "Subscriptions",
    "misc": "Misc",
}

_OTHER_LIKELY = {
    "dining":       ["Shopping", "Personal Care", "Groceries"],
    "personal_care":["Shopping", "Dining", "Gym"],
    "shopping":     ["Personal Care", "Dining", "Entertainment"],
    "groceries":    ["Dining", "Shopping", "Misc"],
    "gym":          ["Personal Care", "Subscriptions", "Entertainment"],
    "entertainment":["Dining", "Shopping", "Subscriptions"],
    "misc":         ["Dining", "Shopping", "Personal Care"],
    "uber_lyft":    ["Gas", "Entertainment", "Misc"],
    "gas":          ["Uber/Lyft", "Misc", "Shopping"],
    "subscriptions":["Entertainment", "Gym", "Misc"],
}


def _reason_phrase(merchant_name: str, suggested: str, amount: float) -> str:
    name_lower = merchant_name.lower()
    is_square = "sq *" in name_lower or "tst*" in name_lower
    display = CATEGORY_DISPLAY.get(suggested, suggested)

    if is_square:
        if amount < 20:
            return "Small Square/POS charge — looks like a coffee shop or quick bite (Dining?)"
        if amount < 60:
            return "Square/POS charge — could be a restaurant or small shop"
        return f"Square/POS charge for ${amount:.0f} — {display} seems most likely"
    if suggested == "personal_care" and amount >= 100:
        return f"${amount:.0f} is typical for a salon, spa, or beauty service"
    if suggested == "personal_care" and amount >= 50:
        return "Amount and name pattern looks like a personal care service"
    if suggested == "dining" and amount < 20:
        return "Small charge — looks like a coffee or quick bite"
    if suggested == "shopping" and amount >= 50:
        return "Amount suggests a retail purchase"
    return f"Name and amount pattern points to {display}"


# ── Clarification message builder ───────────────────────────────────────────

def build_clarification_message(txn: dict, suggested: str) -> str:
    amount = txn["amount"]
    name = txn["name"]
    date = txn.get("date", "recently")

    # Apple / Prime Video amount-specific prompt
    ctype = txn.get("clarification_type", "")
    if ctype == "apple_amount":
        return f"Apple charge ${amount:.2f} — movie/TV rental or subscription?"
    if ctype == "prime_amount":
        return f"Prime Video charge ${amount:.2f} — rental/purchase or subscription?"

    # Ambiguous P2P payment — different prompt
    if ctype == "ambiguous":
        payment_type = txn.get("payment_type", "payment")
        return build_ambiguous_message(name, amount, payment_type)

    display = CATEGORY_DISPLAY.get(suggested, suggested.replace("_", " ").title())
    reason = _reason_phrase(name, suggested, amount)
    others = ", ".join(_OTHER_LIKELY.get(suggested, ["Shopping", "Dining"])[:2])

    return (
        f"I saw a ${amount:.0f} charge from *{name}* on {date}.\n\n"
        f"{reason} — is this *{display}*?\n\n"
        f"Reply with the category ({display}, {others}, or another), "
        f"or reply *ignore* to skip it."
    )


# ── Category reply parser ────────────────────────────────────────────────────

_REPLY_MAP: dict[str, str] = {
    "groceries": "groceries", "grocery": "groceries",
    "dining": "dining", "dining out": "dining", "food": "dining",
    "restaurant": "dining", "coffee": "dining", "eating out": "dining",
    "shopping": "shopping", "clothes": "shopping", "clothing": "shopping",
    "retail": "shopping",
    "personal care": "personal_care", "personal_care": "personal_care",
    "beauty": "personal_care", "salon": "personal_care", "spa": "personal_care",
    "nails": "personal_care", "hair": "personal_care",
    "gas": "gas", "fuel": "gas",
    "uber": "uber_lyft", "lyft": "uber_lyft", "transport": "uber_lyft",
    "transportation": "uber_lyft", "rideshare": "uber_lyft",
    "entertainment": "entertainment", "fun": "entertainment",
    "gym": "gym", "fitness": "gym", "workout": "gym",
    "subscriptions": "subscriptions", "subscription": "subscriptions",
    "misc": "misc", "miscellaneous": "misc", "other": "misc",
    "ignore": "ignore", "skip": "ignore",
    "doesn't matter": "ignore", "dont matter": "ignore",
    "doesnt matter": "ignore", "not important": "ignore",
}


def parse_category_reply(text: str) -> str | None:
    """
    Extract a category or 'ignore' from a reply.
    Returns a category key, 'ignore', or None if the text isn't a category reply.
    """
    t = text.strip().lower().rstrip(".")
    if t in _REPLY_MAP:
        return _REPLY_MAP[t]
    # Longest-match scan
    for phrase, cat in sorted(_REPLY_MAP.items(), key=lambda x: -len(x[0])):
        if phrase in t:
            return cat
    return None


# ── Correction parser ────────────────────────────────────────────────────────

_CORRECTION_PATTERNS = [
    r"actually\s+(?:that\s+was|it\s+was|it'?s|that'?s)\s+(.+)",
    r"that\s+was\s+(.+?)(?:\s+not\s+.+)?$",
    r"it\s+was\s+(.+?)(?:\s+not\s+.+)?$",
    r"wrong[,.]?\s+(?:it'?s|that'?s|its)\s+(.+)",
    r"recategorize\s+(?:as|to)\s+(.+)",
    r"change\s+(?:it\s+)?to\s+(.+)",
    r"should\s+be\s+(.+)",
    r"it'?s\s+(?:actually\s+)?(.+)",
]


def parse_correction(text: str) -> str | None:
    """
    Parse 'actually that was X' and similar correction patterns.
    Returns a new category key or None.
    """
    t = text.strip().lower()
    for pattern in _CORRECTION_PATTERNS:
        m = re.search(pattern, t)
        if m:
            candidate = m.group(1).strip().rstrip(".")
            cat = parse_category_reply(candidate)
            if cat and cat != "ignore":
                return cat
    return None
