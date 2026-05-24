from __future__ import annotations
"""Smart transaction categorization — confidence scoring, merchant memory, clarification flow."""
import re
from financial import CATEGORY_KEYWORDS, PLAID_CATEGORY_MAP, categorize

CONFIDENCE_THRESHOLD = 65  # Below this → ask Ava for clarification

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
