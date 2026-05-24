from __future__ import annotations
"""Law school life coaching — Layer 1 static + Layer 2 dynamic context."""
import re
from datetime import datetime, date
import database
import config

LMU_START = date(2026, 8, 17)

# Layer 1 — static curriculum context
LAYER1_CONTEXT = """
LMU Loyola Law School start date: August 17, 2026.

Typical 1L curriculum:
- Contracts, Torts, Civil Procedure, Constitutional Law, Legal Writing, Property

Semester milestone schedule:
- Week 1: Orientation — get organized, meet classmates, set up your systems
- Week 3: Cold calls begin — brief every case before class, no exceptions
- Week 6: First Legal Writing memo due — start the outline at week 4
- Week 10: Outline season — every class needs a working outline by now
- Week 13-15: Finals — protect sleep, cap social, focus hard
- End of semester: Debrief — what worked, what didn't, adjust for spring

Balance priorities:
- Gym 3x/week minimum — non-negotiable for mental health during 1L
- Cook at home — eating well is not optional when you're under stress
- Social (cap during finals): you need your people, but dial it back weeks 13-15
- Sleep: 7+ hours. Sleep deprivation kills retention. This is not a suggestion.

Academic habits:
- Brief every case before class
- Start outlines early — week 10 is your deadline, not your start date
- Legal Writing is the sleeper hard class — treat it like your hardest class from day one
"""

MILESTONE_WEEKS = {1: "orientation", 3: "cold_calls", 6: "first_memo", 10: "outline_season", 13: "finals", 15: "finals_end"}

# ── Dynamic context update parsers ─────────────────────────────────────────

def parse_context_update(text: str) -> str | None:
    """Try to extract and store a Layer 2 context update. Return confirmation or None."""

    # "Update my classes: [list]"
    m = re.search(r"update\s+my\s+classes?[:\s]+(.+)", text, re.IGNORECASE)
    if m:
        courses = [c.strip() for c in re.split(r"[,;]", m.group(1)) if c.strip()]
        database.set_context("current_courses", courses)
        return f"Got it — I've stored your courses: {', '.join(courses)}. I'll reference these in all coaching from now on."

    # "Add deadline: [assignment] due [date]"
    m = re.search(r"add\s+deadline[:\s]+(.+?)\s+due\s+([\w\s,\-\/]+)", text, re.IGNORECASE)
    if m:
        assignment = m.group(1).strip()
        due_date = m.group(2).strip()
        deadlines = database.get_context("deadlines") or []
        deadlines.append({"assignment": assignment, "due": due_date})
        database.set_context("deadlines", deadlines)
        return f"Stored deadline: *{assignment}* due {due_date}. I'll flag this as it approaches."

    # "My professor for [class] is [name]"
    m = re.search(r"my\s+professor\s+for\s+(.+?)\s+is\s+(.+)", text, re.IGNORECASE)
    if m:
        course = m.group(1).strip()
        professor = m.group(2).strip().rstrip(".")
        profs = database.get_context("professors") or {}
        profs[course] = professor
        database.set_context("professors", profs)
        return f"Noted — {professor} for {course}. I'll keep that in mind."

    # "Exam schedule: [details]"
    m = re.search(r"exam\s+schedule[:\s]+(.+)", text, re.IGNORECASE)
    if m:
        schedule = m.group(1).strip()
        database.set_context("exam_schedule", schedule)
        return f"Exam schedule saved. I'll use this for milestone alerts."

    # "New semester: [details]"
    m = re.search(r"new\s+semester[:\s]+(.+)", text, re.IGNORECASE)
    if m:
        details = m.group(1).strip()
        # Clear previous semester context
        for key in ["current_courses", "deadlines", "professors", "exam_schedule"]:
            database.set_context(key, None)
        database.set_context("semester_notes", details)
        return f"New semester started — cleared previous context. Details saved: {details}"

    return None


# ── Context builder ─────────────────────────────────────────────────────────

def build_coaching_context() -> str:
    """Build the full coaching context — Layer 1 always, Layer 2 if available."""
    ctx = LAYER1_CONTEXT

    dynamic = database.get_all_context()
    if not dynamic:
        return ctx

    ctx += "\n\n--- YOUR CURRENT SEMESTER CONTEXT ---\n"

    courses = dynamic.get("current_courses")
    if courses:
        ctx += f"\nCurrent courses: {', '.join(courses)}"

    profs = dynamic.get("professors")
    if profs:
        prof_lines = ", ".join(f"{cls} ({prof})" for cls, prof in profs.items())
        ctx += f"\nProfessors: {prof_lines}"

    deadlines = dynamic.get("deadlines")
    if deadlines:
        ctx += "\nUpcoming deadlines:"
        for d in deadlines:
            ctx += f"\n  - {d['assignment']} due {d['due']}"

    exam_schedule = dynamic.get("exam_schedule")
    if exam_schedule:
        ctx += f"\nExam schedule: {exam_schedule}"

    return ctx


# ── Proactive messages ──────────────────────────────────────────────────────

def get_current_week() -> int | None:
    """Return week number since LMU start, or None if not started."""
    today = date.today()
    if today < LMU_START:
        return None
    delta = (today - LMU_START).days
    return (delta // 7) + 1


def build_sunday_checkin() -> str:
    week = get_current_week()
    if week is None:
        days_until = (LMU_START - date.today()).days
        return (
            f"Law school starts in {days_until} days. This is your final stretch — "
            "financial habits, checklist, and mental readiness. What's your focus this week?"
        )

    dynamic = database.get_all_context()
    courses = dynamic.get("current_courses", ["Contracts", "Torts", "Civil Procedure", "Constitutional Law", "Legal Writing", "Property"])
    deadlines = dynamic.get("deadlines", [])

    lines = [f"Week {week} check-in — what's ahead:\n"]

    # Milestone alert
    for milestone_week, milestone_name in MILESTONE_WEEKS.items():
        if week == milestone_week:
            lines.append(_milestone_message(milestone_name))
            lines.append("")
            break

    lines.append(f"Your courses: {', '.join(courses)}")

    if deadlines:
        lines.append("\nUpcoming deadlines:")
        for d in deadlines[:3]:
            lines.append(f"  • {d['assignment']} — {d['due']}")

    lines.append("\nWhat's your plan for this week? Any classes or assignments you're worried about?")
    return "\n".join(lines)


def build_midweek_checkin() -> str:
    recent = database.get_recent_checkins(limit=2)
    gym_flag = ""
    for c in recent:
        if "gym" in c.get("notes", "").lower() and ("haven't" in c["notes"].lower() or "not been" in c["notes"].lower()):
            gym_flag = " Last week you mentioned skipping the gym — how are you doing with that?"

    return (
        f"Midweek check — quick pulse:\n\n"
        f"1. Gym this week? (target: 3x){gym_flag}\n"
        f"2. Cooking at home or eating out?\n"
        f"3. Sleep — are you getting 7+ hours?\n"
        f"4. Anything feeling overwhelming right now?\n\n"
        f"No judgment, just honest check-in."
    )


def _milestone_message(name: str) -> str:
    messages = {
        "orientation": "Week 1 — Orientation. Meet everyone, get your systems set up, and don't stress about the material yet. Focus on logistics.",
        "cold_calls": "Week 3 — Cold calls start this week. Brief every single case before class. Every one. No exceptions. This is the habit that will define your first semester.",
        "first_memo": "Week 6 — Your first Legal Writing memo is coming. If you haven't started outlining, do it today. Not tomorrow.",
        "outline_season": "Week 10 — Outline season. Every class needs a working outline now. If you're behind, this week is your catch-up window.",
        "finals": "Weeks 13-15 — Finals. Cap social, protect sleep, and work your outlines. You've been preparing for this.",
        "finals_end": "End of semester. Decompress, then debrief: what worked, what didn't, what you're changing next semester.",
    }
    return messages.get(name, "")


# ── Stress detection ────────────────────────────────────────────────────────

STRESS_KEYWORDS = [
    "overwhelmed", "stressed", "anxious", "can't handle", "drowning",
    "falling behind", "breaking down", "panic", "scared", "exhausted",
    "burned out", "burnout", "i can't", "too much", "losing it"
]


def detect_stress(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in STRESS_KEYWORDS)


def get_stress_response(text: str) -> str:
    """Warmth first, practical second."""
    return (
        "That sounds really hard. 1L is genuinely one of the most intense things people do — "
        "feeling overwhelmed doesn't mean you can't handle it, it means you're human.\n\n"
        "Take a breath. What's the one thing that feels most urgent right now? "
        "Let's just start there."
    )
