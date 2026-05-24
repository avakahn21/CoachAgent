from __future__ import annotations
"""Pre-law checklist command parsing and execution — all factual queries handled in Python."""
import re
from datetime import datetime
import sheets
import database


# ── Formatting helpers ───────────────────────────────────────────────────────

def _task_line(t: dict) -> str:
    dl = t.get("Deadline", "").strip()
    name = t.get("Task", "Unknown")
    return f"{name} — due {dl}" if dl else name


def _is_urgent(t: dict) -> bool:
    p = t.get("Priority", "").lower()
    return "urgent" in p or "🔴" in t.get("Priority", "")


# ── Factual query handlers (no Claude) ──────────────────────────────────────

def handle_due_query(text: str) -> str | None:
    """Handle 'what's due', 'urgent', 'overdue', 'what's next', 'this week/month'."""
    t = text.lower()
    triggers = [
        "what's due", "whats due", "what is due", "due this", "what's next",
        "whats next", "what's urgent", "whats urgent", "urgent tasks", "urgent",
        "overdue", "coming up", "due soon", "this week", "show tasks",
        "show me tasks", "list tasks", "my tasks", "what do i have",
        "what tasks",
    ]
    if not any(kw in t for kw in triggers):
        return None

    today = datetime.utcnow()
    incomplete = sheets.get_incomplete_tasks()

    if "overdue" in t:
        overdue = [
            task for task in incomplete
            if (dl := sheets._parse_deadline(task["Deadline"])) and dl < today
        ]
        if not overdue:
            return "Nothing overdue — you're current."
        lines = [f"{task['Task']} — was due {task['Deadline']}" for task in overdue[:6]]
        return "Overdue:\n" + "\n".join(lines)

    if "urgent" in t:
        urgent = [task for task in incomplete if _is_urgent(task)]
        if not urgent:
            return "No urgent items flagged right now."
        lines = [_task_line(task) for task in urgent[:6]]
        return "Urgent:\n" + "\n".join(lines)

    if "this month" in t:
        tasks = sheets.get_tasks_due_this_month()
        if not tasks:
            return "Nothing due this month."
        lines = [_task_line(task) for task in tasks[:8]]
        return "Due this month:\n" + "\n".join(lines)

    # Default: next 7 days
    upcoming = sheets.get_upcoming_tasks(days=7)
    if not upcoming:
        # Widen to 14 days if nothing in 7
        upcoming = sheets.get_upcoming_tasks(days=14)
        if not upcoming:
            return "Nothing due in the next two weeks."
        lines = [_task_line(task) for task in upcoming[:5]]
        return "Nothing due this week, but coming up soon:\n" + "\n".join(lines)
    lines = [_task_line(task) for task in upcoming[:5]]
    return "Due this week:\n" + "\n".join(lines)


def handle_status_query(text: str) -> str | None:
    """Handle 'status', 'progress', 'how am I doing', 'what's left'."""
    t = text.lower()
    triggers = [
        "status", "progress", "how am i doing", "what's left", "whats left",
        "how many tasks", "how far", "where am i", "how many left",
        "how many done", "how many complete",
    ]
    if not any(kw in t for kw in triggers):
        return None

    all_tasks = sheets.load_checklist()
    completed = sheets.get_completed_tasks()
    incomplete = sheets.get_incomplete_tasks()
    urgent = [task for task in incomplete if _is_urgent(task)]

    n_total = len(all_tasks)
    n_done = len(completed)
    n_left = len(incomplete)

    msg = f"{n_done} of {n_total} tasks done, {n_left} left."
    if urgent:
        top = urgent[0]
        msg += f" {len(urgent)} urgent — most pressing is {top['Task']} (due {top['Deadline']})."
    else:
        msg += " No urgent items flagged."
    return msg


# ── Existing command parsers ─────────────────────────────────────────────────

def handle_mark_complete(text: str) -> str | None:
    m = re.search(r"mark\s+(.+?)\s+(?:as\s+)?(?:complete|done|finished)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"(?:finished|completed|done with)\s+(.+)", text, re.IGNORECASE)
    if not m:
        return None
    task_name = m.group(1).strip().rstrip(".")
    result = sheets.mark_task_complete(task_name)
    database.save_message("system", f"Marked complete: {task_name}", mode="checklist")

    next_tasks = sheets.get_upcoming_tasks(days=7)
    next_str = ""
    if next_tasks:
        top = next_tasks[0]
        next_str = f"\n\nNext up: {top.get('Task', '')} — due {top.get('Deadline', 'TBD')}."
    return f"Done. {result}{next_str}"


def handle_push_deadline(text: str) -> str | None:
    m = re.search(
        r"push\s+(.+?)\s+deadline\s+to\s+([\w\s,\-\/]+?)(?:,\s*(.+))?$",
        text, re.IGNORECASE
    )
    if not m:
        return None
    task_name = m.group(1).strip()
    new_deadline_raw = m.group(2).strip().rstrip(",")
    reason = (m.group(3) or "").strip()
    new_deadline = _parse_date(new_deadline_raw) or new_deadline_raw
    result = sheets.update_task_deadline(task_name, new_deadline, reason)
    return f"{result}" + (f" Reason noted: {reason}." if reason else "")


def handle_add_task(text: str) -> str | None:
    m = re.search(
        r"add\s+task[:\s]+(.+?)\s+due\s+([\w\s,\-\/]+?),\s*priority\s+(\w+),\s*category\s+(\w+)",
        text, re.IGNORECASE
    )
    if not m:
        return None
    task_name = m.group(1).strip()
    deadline_raw = m.group(2).strip()
    priority = m.group(3).strip().lower()
    category = m.group(4).strip().lower()
    deadline = _parse_date(deadline_raw) or deadline_raw
    result = sheets.add_task(task_name, deadline, priority, category)
    return result


def handle_remove_task(text: str) -> str | None:
    m = re.search(r"remove\s+(?:task\s+)?(.+)", text, re.IGNORECASE)
    if not m:
        return None
    task_name = m.group(1).strip().rstrip(".")
    return sheets.remove_task(task_name)


def handle_whats_due_this_month(text: str) -> str | None:
    keywords = ["due this month", "what's still due", "whats still due", "what is due this month"]
    if not any(kw in text.lower() for kw in keywords):
        return None
    tasks = sheets.get_tasks_due_this_month()
    if not tasks:
        return "Nothing due this month — you're clear."
    lines = [_task_line(t) for t in tasks]
    return "Due this month:\n" + "\n".join(lines)


def handle_what_completed(text: str) -> str | None:
    keywords = ["what have i completed", "what did i complete", "completed so far",
                "what's done", "whats done", "what have i done"]
    if not any(kw in text.lower() for kw in keywords):
        return None
    tasks = sheets.get_completed_tasks()
    if not tasks:
        return "Nothing marked complete yet."
    lines = [f"✓ {t.get('Task', 'Unknown')}" for t in tasks]
    return f"{len(tasks)} completed:\n" + "\n".join(lines)


def try_checklist_command(text: str) -> str | None:
    """
    Try all Python checklist handlers in order.
    Returns a response string if handled, None if Claude should take it.
    """
    for handler in [
        handle_mark_complete,
        handle_push_deadline,
        handle_add_task,
        handle_remove_task,
        handle_whats_due_this_month,
        handle_what_completed,
        handle_due_query,
        handle_status_query,
    ]:
        result = handler(text)
        if result:
            return result
    return None


# ── Monday checklist digest (scheduled) ─────────────────────────────────────

def build_monday_digest() -> str:
    tasks = sheets.get_upcoming_tasks(days=14)[:3]
    if not tasks:
        return "No urgent checklist items this week."
    lines = ["Your top checklist items this week:\n"]
    for t in tasks:
        name = t.get("Task", "Unknown")
        deadline = t.get("Deadline", "TBD")
        lines.append(f"• {name} — due {deadline}")
    return "\n".join(lines)


# ── Date normalization ───────────────────────────────────────────────────────

def _parse_date(raw: str) -> str | None:
    formats = ["%B %d %Y", "%B %d, %Y", "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%B %d"]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.utcnow().year)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
