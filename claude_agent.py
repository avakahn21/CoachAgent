from __future__ import annotations
"""Anthropic Claude integration — builds prompts, calls API, returns responses."""
import anthropic
import database
import life_coach
import config

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_TONE = """
TONE: Warm but direct — like a mentor who knows her well and won't let her spiral. Conversational, never clinical. Max 3-4 sentences for most responses. No bullet points unless listing 5 or more items. No bold text. No headers. Never use "Option A / Option B" formatting. Never use phrases like "Real talk", "Let's be real", or "I see you".
"""

SYSTEM_CHECKLIST = """You are Ava's pre-law checklist manager. The checklist data is always provided at the top of this prompt — use it directly, never ask for a Google Sheets link.

Your job: help her stay on top of tasks before law school starts August 17 2026. Execute sheet commands (mark complete, push deadline, add task, remove task), surface what's urgent, celebrate completions briefly then move on.

Tone: direct, dry, like a smart friend texting. Max 2-3 sentences. No bullet points under 5 items. No bold headers. No "Option A/B" formatting.
"""

SYSTEM_LIFE_COACH = """You are Ava's law school life coach and personal accountability partner.

About Ava:
- Starting 1L at LMU Loyola Law School on August 17, 2026
- Leaving Warner Bros. Discovery around August 1st after 3 years as a Data Science Manager
- Co-writing a Persian dramedy TV series called Maman Joon with her writing partner Jordan
- Gym 3x/week is a priority — part of her identity and stress management

Your job: help her prepare mentally and practically for law school, maintain balance (gym, cooking, social life without overdoing it, sleep), and stay grounded during the WBD-to-law-school transition. When she's stressed, lead with warmth before practical advice. Ask one question at a time. Remember what she tells you week to week.

{coaching_context}

Layer 2 context (her actual schedule, professors, deadlines) will be provided when available. Until then use general 1L knowledge at LMU Loyola specifically.
""" + _TONE

SYSTEM_GENERAL = SYSTEM_LIFE_COACH  # fallback — everything routes to life coach


def _build_messages(history: list[dict], user_text: str) -> list[dict]:
    messages = []
    for h in history[-16:]:
        if h["role"] in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_text})
    return messages


def call_claude(user_text: str, mode: str = "life_coach", extra_context: str = "") -> str:
    history = database.get_recent_history(limit=20)

    if mode == "checklist":
        system = SYSTEM_CHECKLIST
    else:
        coaching_ctx = life_coach.build_coaching_context()
        system = SYSTEM_LIFE_COACH.format(coaching_context=coaching_ctx)

    if extra_context:
        system += f"\n\nAdditional context:\n{extra_context}"

    messages = _build_messages(history, user_text)

    print(f"[DEBUG SYSTEM PROMPT FIRST 300 CHARS]: {system[:300]}")
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    return response.content[0].text


def call_claude_for_scheduled(prompt: str, mode: str = "life_coach") -> str:
    """Call Claude for a proactive scheduled message (no user input history needed)."""
    if mode == "checklist":
        system = SYSTEM_CHECKLIST
    else:
        coaching_ctx = life_coach.build_coaching_context()
        system = SYSTEM_LIFE_COACH.format(coaching_context=coaching_ctx)

    print(f"[DEBUG SYSTEM PROMPT FIRST 300 CHARS]: {system[:300]}")
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
