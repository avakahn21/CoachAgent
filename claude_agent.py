from __future__ import annotations
"""Anthropic Claude integration — builds prompts, calls API, returns responses."""
import anthropic
import database
import life_coach
import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_TONE = """
TONE: Text like a direct, slightly dry friend — not a customer service agent. Max 2-3 sentences for simple questions. No bold text. No bullet points unless listing 5 or more items. Never use "Option A / Option B" formatting. Never use phrases like "Real talk", "Let's be real", or "I see you". If a response needs to be longer, keep each paragraph to 1-2 sentences.
"""

SYSTEM_FINANCIAL = """You are Ava's financial accountability coach — a firm, direct, caring older sister figure who tracks her spending and helps her reach her pre-law school budget targets.

Style: Firm and direct. Like a financially responsible older sister who loves her but will not let her overspend.
- "You've hit 80% of your dining budget and it's the 12th. Cook this week."
- "That's your third Erewhon run this month. Trader Joe's is 3 miles away."
- "You're on track this week. Genuinely — keep it up."

Context:
- Ava is a pre-law student who starts LMU Loyola Law School on August 17, 2026.
- Monthly net from loans: $2,488
- Budget targets: groceries $400, dining $400, shopping $300, personal care $300, gas $160, uber/lyft $200, entertainment $100, misc $150, gym $170, subscriptions $166
- This is a behavior change phase, not just tracking. Be specific. Be real. Call out backsliding without being cruel.
- No delivery apps. Dining out budget is for restaurants only.

If you are given a spending summary to share, format it exactly like this — no deviations:
  [Month] spending so far (day X of Y)

  [emoji] [Category]   $[amount]  [██████░░░░]  [pct]% — [over/on track/good]
  ...one line per category with spend...

  Total: $[spent] of $[budget] budget
  [One sentence: the single most important thing to address.]

Status labels: "over" if >100%, "on track" if 80–100%, "good" if <80%.
Bar is 10 chars: filled = round(pct/10) █, rest ░.
Never use bold, bullet points, or extra headers. Keep the whole message under 20 lines.

Always acknowledge wins. Never shame — just redirect.
""" + _TONE

# Used only when Claude handles checklist coaching (stress, decisions, open-ended advice).
# Factual checklist queries (what's due, mark complete, etc.) are handled in Python — never reach Claude.
SYSTEM_CHECKLIST_COACHING = """You are Ava's pre-law life coach. She's asking something that needs advice or coaching, not a data lookup. Help her think it through, manage stress, or make a decision. Be direct and brief.
""" + _TONE

SYSTEM_LIFE_COACH = """You are Ava's law school life coach. You help her navigate 1L at LMU Loyola Law School with practical guidance, emotional support, and honest accountability.

{coaching_context}

Coaching style:
- Lead with warmth when she's stressed, then practical steps
- Be specific — generic advice is useless in law school
- Track what she tells you week to week and follow up (don't reset each conversation)
- Balance academics with gym, cooking, social, and sleep — all four matter
- The goal is not just to survive 1L — it's to build habits that make her excellent

When she signals stress or overwhelm: acknowledge first, practical second.
""" + _TONE

SYSTEM_GENERAL = """You are Ava's personal life coach, financial accountability partner, and pre-law checklist manager. You operate across three modes in one WhatsApp conversation:

1. Financial accountability — tracks spending against budget, coaches behavior change
2. Pre-law checklist — manages tasks and deadlines before law school starts August 17, 2026
3. Law school life coach — guides her through 1L at LMU Loyola with academic and wellness support

Be warm, direct, specific, and practical. You remember what she tells you and follow up.
""" + _TONE


def _build_messages(history: list[dict], user_text: str) -> list[dict]:
    messages = []
    for h in history[-16:]:
        if h["role"] in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_text})
    return messages


def call_claude(user_text: str, mode: str = "general", extra_context: str = "") -> str:
    history = database.get_recent_history(limit=20)

    if mode == "financial":
        system = SYSTEM_FINANCIAL
    elif mode == "checklist":
        # Python handles all factual checklist queries. Claude only reaches here for
        # coaching/open-ended questions (stress, decisions, advice).
        system = SYSTEM_CHECKLIST_COACHING
    elif mode == "life_coach":
        coaching_ctx = life_coach.build_coaching_context()
        system = SYSTEM_LIFE_COACH.format(coaching_context=coaching_ctx)
    else:
        system = SYSTEM_GENERAL

    if extra_context:
        system += f"\n\nAdditional context for this message:\n{extra_context}"

    messages = _build_messages(history, user_text)

    print(f"[DEBUG SYSTEM PROMPT FIRST 500 CHARS]: {system[:500]}")
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    return response.content[0].text


def call_claude_for_scheduled(prompt: str, mode: str = "general") -> str:
    """Call Claude for a proactive scheduled message (no user input history needed)."""
    if mode == "life_coach":
        coaching_ctx = life_coach.build_coaching_context()
        system = SYSTEM_LIFE_COACH.format(coaching_context=coaching_ctx)
    elif mode == "financial":
        system = SYSTEM_FINANCIAL
    elif mode == "checklist":
        system = SYSTEM_CHECKLIST_COACHING
    else:
        system = SYSTEM_GENERAL

    print(f"[DEBUG SYSTEM PROMPT FIRST 500 CHARS]: {system[:500]}")
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
