from __future__ import annotations
"""Financial coaching logic — spend parsing, budget alerts, weekly/monthly reports."""
import re
from datetime import datetime
import database
import sheets
import config

# ── Manual spend parsing ────────────────────────────────────────────────────

CATEGORY_KEYWORDS = {
    "groceries": ["trader joe", "whole foods", "safeway", "ralphs", "vons", "grocery", "groceries", "market", "aldi", "kroger"],
    "dining": ["restaurant", "nobu", "erewhon", "coffee", "cafe", "café", "sushi", "pizza", "taco", "burger", "dine", "dining", "brunch", "lunch", "dinner", "breakfast", "bar ", "boba"],
    "shopping": ["amazon", "zara", "h&m", "nordstrom", "target", "tj maxx", "ross", "aritzia", "revolve", "shein", "clothing", "clothes", "shoes", "bag", "shop"],
    "personal_care": ["salon", "haircut", "nails", "spa", "massage", "skincare", "sephora", "ulta", "beauty", "wax", "facial"],
    "gas": ["gas", "chevron", "shell", "arco", "76", "mobil", "exxon", "fuel", "bp"],
    "uber_lyft": ["uber", "lyft", "rideshare", "ride"],
    "entertainment": ["movie", "cinema", "concert", "ticket", "netflix", "spotify", "hulu", "disney", "show", "event", "club"],
    "gym": ["gym", "equinox", "la fitness", "planet fitness", "classpass", "pilates", "yoga", "workout"],
    "subscriptions": ["subscription", "monthly", "annual", "apple", "google", "adobe"],
}


def categorize(description: str) -> str:
    desc_lower = description.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower:
                return category
    return "misc"


# Plaid personal_finance_category primary values → our budget categories
PLAID_CATEGORY_MAP = {
    "FOOD_AND_DRINK": "dining",
    "GROCERIES": "groceries",
    "GENERAL_MERCHANDISE": "shopping",
    "APPAREL_AND_ACCESSORIES": "shopping",
    "PERSONAL_CARE": "personal_care",
    "TRANSPORTATION": "uber_lyft",
    "GAS_STATIONS": "gas",
    "ENTERTAINMENT": "entertainment",
    "GYMS_AND_FITNESS_CENTERS": "gym",
    "SUBSCRIPTION": "subscriptions",
    "RENT_AND_UTILITIES": "misc",
}


def categorize_plaid(merchant_name: str, plaid_category: str) -> str:
    """Categorize a Plaid transaction — merchant name wins, then Plaid category."""
    # Merchant name matching takes priority (more accurate for our specific budget)
    by_name = categorize(merchant_name)
    if by_name != "misc":
        return by_name
    # Fall back to Plaid's own category
    return PLAID_CATEGORY_MAP.get(plaid_category.upper(), "misc")


# Regex to parse "I spent $X at/on Y" and variants
SPEND_PATTERNS = [
    r"(?:i\s+)?spent\s+\$?([\d,]+(?:\.\d{1,2})?)\s+(?:at|on|for)\s+(.+)",
    r"(?:i\s+)?paid\s+\$?([\d,]+(?:\.\d{1,2})?)\s+(?:at|on|for)\s+(.+)",
    r"(?:i\s+)?bought\s+(.+?)\s+for\s+\$?([\d,]+(?:\.\d{1,2})?)",
    r"\$?([\d,]+(?:\.\d{1,2})?)\s+(?:at|on|for)\s+(.+)",
]


def parse_manual_spend(text: str) -> tuple[float, str, str] | None:
    """Returns (amount, description, category) or None."""
    text = text.strip()
    for pattern in SPEND_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            if "bought" in pattern:
                description, amount_str = groups[0].strip(), groups[1]
            else:
                amount_str, description = groups[0], groups[1].strip()
            amount = float(amount_str.replace(",", ""))
            description = description.rstrip(".")
            category = categorize(description)
            return amount, description, category
    return None


def log_manual_spend(text: str) -> str | None:
    """Parse free-text spend, log to DB and sheet. Returns coach response or None."""
    result = parse_manual_spend(text)
    if result is None:
        return None
    amount, description, category = result
    date_str = datetime.utcnow().date().isoformat()
    database.log_spend(amount, category, description, date_str)
    sheets.write_monthly_actual(category, amount)

    budget = config.BUDGET_TARGETS.get(category, 0)
    total = database.get_category_spend_this_month(category)
    pct = (total / budget * 100) if budget else 0
    now = datetime.utcnow()

    response = f"Logged: ${amount:.2f} at {description} → {category.replace('_', ' ').title()}.\n"
    response += f"Running total this month: ${total:.2f} / ${budget} ({pct:.0f}%)."

    if budget and pct >= 100:
        response += f"\n\nYou've hit your {category.replace('_', ' ')} budget for the month. No more {category.replace('_', ' ')} spending."
    elif budget and pct >= 75 and now.day < 20:
        response += f"\n\nHead's up — you're at {pct:.0f}% of your {category.replace('_', ' ')} budget and it's only the {now.day}th. Slow down."

    return response


# ── Budget alerts ───────────────────────────────────────────────────────────

def check_threshold_alerts() -> list[str]:
    """Return alert messages for any category at 75%+ before the 20th."""
    now = datetime.utcnow()
    if now.day >= 20:
        return []
    alerts = []
    totals = database.get_all_category_totals_this_month()
    for category, budget in config.BUDGET_TARGETS.items():
        spent = totals.get(category, 0)
        pct = (spent / budget * 100) if budget else 0
        if pct >= 75:
            alerts.append(
                f"Alert: {category.replace('_', ' ').title()} is at {pct:.0f}% (${spent:.0f}/${budget}) and it's only the {now.day}th."
            )
    return alerts


# ── Weekly report ───────────────────────────────────────────────────────────

def build_weekly_report() -> str:
    totals = database.get_all_category_totals_this_month()
    now = datetime.utcnow()
    lines = [f"Weekly spending snapshot — {now.strftime('%B %d')}:\n"]

    worst_category = None
    worst_pct = 0

    for category, budget in config.BUDGET_TARGETS.items():
        spent = totals.get(category, 0)
        if spent == 0:
            continue
        pct = (spent / budget * 100) if budget else 0
        bar = "🔴" if pct >= 100 else ("🟡" if pct >= 75 else "🟢")
        lines.append(f"{bar} {category.replace('_', ' ').title()}: ${spent:.0f} / ${budget} ({pct:.0f}%)")
        if pct > worst_pct:
            worst_pct = pct
            worst_category = category

    if worst_category:
        lines.append(f"\nBiggest overspend: {worst_category.replace('_', ' ').title()} at {worst_pct:.0f}%.")
        lines.append(_get_category_suggestion(worst_category))

    return "\n".join(lines)


def build_monthly_report() -> str:
    totals = database.get_all_category_totals_this_month()
    now = datetime.utcnow()
    total_spent = sum(totals.values())
    lines = [f"Monthly review — {now.strftime('%B %Y')}:\n"]
    lines.append(f"Total spent: ${total_spent:.0f} / ${config.MONTHLY_NET} net\n")

    over = []
    on_track = []
    for category, budget in config.BUDGET_TARGETS.items():
        spent = totals.get(category, 0)
        pct = (spent / budget * 100) if budget else 0
        if pct >= 90:
            over.append(f"  ❌ {category.replace('_', ' ').title()}: ${spent:.0f} / ${budget}")
        elif pct <= 60:
            on_track.append(f"  ✅ {category.replace('_', ' ').title()}: ${spent:.0f} / ${budget}")

    if on_track:
        lines.append("Wins:\n" + "\n".join(on_track))
    if over:
        lines.append("\nFlags:\n" + "\n".join(over))

    savings = config.MONTHLY_NET - total_spent
    lines.append(f"\nSavings runway: ${max(savings, 0):.0f} remaining this month.")
    return "\n".join(lines)


def _get_category_suggestion(category: str) -> str:
    suggestions = {
        "shopping": "Concrete step: put your card in the freezer metaphorically — unsubscribe from all retail emails this week.",
        "dining": "Concrete step: pick 3 nights this week to cook at home. Batch cook Sunday so the excuse 'nothing to eat' doesn't fly.",
        "personal_care": "Concrete step: audit your standing beauty appointments. Which ones are truly non-negotiable this month?",
        "gym": "Concrete step: you should have one gym membership only. Cancel duplicates today.",
        "uber_lyft": "Concrete step: plan your week's trips in advance. Can 2 of them be driving yourself?",
        "groceries": "Concrete step: meal plan before you shop. One list, one trip, done.",
        "entertainment": "Concrete step: free events exist. Check your city's calendar before buying tickets.",
    }
    return suggestions.get(category, "Concrete step: review this category and cut one recurring expense this week.")


# ── Weekly coaching challenge ───────────────────────────────────────────────

def get_weekly_challenge() -> str:
    """Identify highest gap category and return a specific challenge."""
    totals = database.get_all_category_totals_this_month()
    gaps = {}
    for category, avg in config.CURRENT_AVERAGES.items():
        budget = config.BUDGET_TARGETS.get(category, avg)
        gaps[category] = avg - budget

    top_category = max(gaps, key=lambda c: gaps[c])
    avg = config.CURRENT_AVERAGES[top_category]
    target = config.BUDGET_TARGETS.get(top_category, avg)

    challenges = {
        "shopping": f"Your shopping average is ${avg:,}/month. Target is ${target}. This week: no new clothing or accessory purchases — not even browsing. Can you do it?",
        "dining": f"Your dining average is ${avg:,}/month. Target is ${target}. This week: cook 4 dinners at home. Can you do it?",
        "personal_care": f"Your personal care average is ${avg:,}/month. Target is ${target}. This week: cancel or reschedule any non-essential appointments. Can you cut one?",
        "gym": f"Your fitness spend is ${avg:,}/month. Target is ${target}. This week: identify every gym/fitness membership and cancel all but one. Can you do it today?",
        "uber_lyft": f"Your transport average is ${avg:,}/month. Target is ${target}. This week: drive yourself for at least half your trips. Can you do it?",
        "groceries": f"Your grocery average is ${avg:,}/month. Target is ${target}. This week: meal plan before shopping. One trip, stick to the list. Can you do it?",
    }
    return challenges.get(top_category, f"Focus on {top_category.replace('_', ' ')} this week — it's your biggest gap.")
