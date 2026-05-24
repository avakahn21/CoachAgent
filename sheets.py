from __future__ import annotations
"""Google Sheets integration — reads checklist/budget, writes back on commands."""
import json
import os
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_gc = None
_spreadsheet = None

# Column indices (0-based) matching actual sheet layout A–F
COL_COMPLETED = 0  # A — completion status (Yes / blank)
COL_TASK_NAME = 1  # B — task name
COL_NOTES = 2      # C — details / notes
COL_DEADLINE = 3   # D — deadline (YYYY-MM-DD)
COL_PRIORITY = 4   # E — priority
COL_CATEGORY = 5   # F — category

# Rows whose column B exactly matches one of these are section label rows, not tasks
_SECTION_HEADERS = {
    "FINANCIAL", "LMU ENROLLMENT", "WAITLIST", "BOOKS",
    "HEALTH", "LIFE ADMIN",
}

# Literal strings that mark non-task rows (header repeats, titles, instructions)
_SKIP_EXACT = {"task", "✓", "deadline"}
_SKIP_PREFIXES = ("pre-law school", "type ", "type yes", "before august")


def _get_gc():
    global _gc
    if _gc is None:
        creds_raw = config.GOOGLE_SHEETS_CREDENTIALS
        print(f"[sheets] GOOGLE_SHEETS_CREDENTIALS = {creds_raw!r}")
        if os.path.isfile(creds_raw):
            print(f"[sheets] Loading credentials from file: {creds_raw}")
            creds = Credentials.from_service_account_file(creds_raw, scopes=SCOPES)
            with open(creds_raw) as f:
                svc = json.load(f)
            print(f"[sheets] Service account email: {svc.get('client_email', 'unknown')}")
        else:
            print("[sheets] GOOGLE_SHEETS_CREDENTIALS is not a file path — parsing as JSON")
            creds_info = json.loads(creds_raw)
            print(f"[sheets] Service account email: {creds_info.get('client_email', 'unknown')}")
            creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        _gc = gspread.authorize(creds)
        print("[sheets] gspread authorized successfully")
    return _gc


def _get_sheet(tab_name: str):
    global _spreadsheet
    gc = _get_gc()
    if _spreadsheet is None:
        _spreadsheet = gc.open_by_key(config.SPREADSHEET_ID)
    return _spreadsheet.worksheet(tab_name)


def _pad(row: list, length: int) -> list:
    """Ensure row has at least `length` elements (avoids index errors on short rows)."""
    return row + [""] * max(0, length - len(row))


def _is_skippable_row(task_name: str) -> bool:
    """True for section labels, column-header repeats, title rows, and instruction rows."""
    lower = task_name.lower()
    upper = task_name.upper()
    if lower in _SKIP_EXACT:
        return True
    if any(lower.startswith(p) for p in _SKIP_PREFIXES):
        return True
    return upper in _SECTION_HEADERS or any(
        upper == h or upper.startswith(h + " ") or upper.startswith(h + ":")
        for h in _SECTION_HEADERS
    )


# ── Read operations ─────────────────────────────────────────────────────────

def _row_to_task(row: list) -> dict:
    row = _pad(row, 6)
    return {
        "Completed": row[COL_COMPLETED],
        "Task": row[COL_TASK_NAME],
        "Details / Notes": row[COL_NOTES],
        "Deadline": row[COL_DEADLINE],
        "Priority": row[COL_PRIORITY],
        "Category": row[COL_CATEGORY],
    }


def load_checklist() -> list[dict]:
    """Return real task rows as dicts, skipping the header, blank rows, and section labels."""
    ws = _get_sheet(config.SHEET_CHECKLIST_TAB)
    all_rows = ws.get_all_values()
    tasks = []
    for row in all_rows[1:]:  # row 0 is the header
        row = _pad(row, 6)
        task_name = row[COL_TASK_NAME].strip()
        if not task_name or _is_skippable_row(task_name):
            continue
        tasks.append(_row_to_task(row))
    return tasks


def load_monthly_budget() -> list[dict]:
    """Return budget rows as dicts with keys Category, Budget, Actual."""
    ws = _get_sheet(config.SHEET_MONTHLY_BUDGET_TAB)
    all_rows = ws.get_all_values()
    result = []
    for row in all_rows[1:]:  # skip header
        row = _pad(row, 3)
        category = row[0].strip()
        if not category:
            continue
        def _num(s: str) -> float:
            try:
                return float(str(s).replace(",", "").replace("$", "")) if s else 0.0
            except ValueError:
                return 0.0
        result.append({
            "Category": category,
            "Budget": _num(row[1]),
            "Actual": _num(row[2]),
        })
    return result


_DATE_FORMATS = ["%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%B %d", "%b %d"]


def _parse_deadline(raw: str) -> datetime | None:
    """Try multiple date formats; infer year for month-day-only strings."""
    raw = raw.strip().rstrip("—").strip()
    if not raw:
        return None
    now = datetime.utcnow()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:  # strptime default when year not in format
                # Assign the next occurrence of that month/day
                dt = dt.replace(year=now.year)
                if dt < now:
                    dt = dt.replace(year=now.year + 1)
            return dt
        except ValueError:
            pass
    return None


def get_incomplete_tasks() -> list[dict]:
    tasks = load_checklist()
    return [t for t in tasks if t["Completed"].strip().lower() not in ("yes", "true", "1")]


def get_tasks_due_this_month() -> list[dict]:
    now = datetime.utcnow()
    result = []
    for t in get_incomplete_tasks():
        dl = _parse_deadline(t["Deadline"])
        if dl and dl.year == now.year and dl.month == now.month:
            result.append(t)
    return result


def get_completed_tasks() -> list[dict]:
    tasks = load_checklist()
    return [t for t in tasks if t["Completed"].strip().lower() in ("yes", "true", "1")]


def get_upcoming_tasks(days: int = 14) -> list[dict]:
    from datetime import timedelta
    now = datetime.utcnow()
    cutoff = now + timedelta(days=days)
    result = []
    for t in get_incomplete_tasks():
        dl = _parse_deadline(t["Deadline"])
        if dl and dl <= cutoff:
            result.append(t)
    result.sort(key=lambda t: _parse_deadline(t["Deadline"]) or datetime.max)
    return result


# ── Write operations ────────────────────────────────────────────────────────

def _find_task_row(task_name: str) -> tuple[gspread.Worksheet, int] | tuple[None, None]:
    """Return (worksheet, 1-based row index) for the first row whose task name matches."""
    ws = _get_sheet(config.SHEET_CHECKLIST_TAB)
    all_rows = ws.get_all_values()
    task_lower = task_name.lower()
    for i, row in enumerate(all_rows):
        row = _pad(row, 6)
        cell = row[COL_TASK_NAME].strip()
        if cell.lower() == task_lower or task_lower in cell.lower():
            return ws, i + 1  # 1-based row number
    return None, None


def mark_task_complete(task_name: str) -> str:
    ws, row = _find_task_row(task_name)
    if ws is None:
        return f"Task '{task_name}' not found in the sheet."
    ws.update_cell(row, COL_COMPLETED + 1, "Yes")
    return f"Marked '{task_name}' as complete in the sheet."


def update_task_deadline(task_name: str, new_deadline: str, reason: str = "") -> str:
    ws, row = _find_task_row(task_name)
    if ws is None:
        return f"Task '{task_name}' not found in the sheet."
    ws.update_cell(row, COL_DEADLINE + 1, new_deadline)
    if reason:
        existing_note = ws.cell(row, COL_NOTES + 1).value or ""
        note_entry = f"[{datetime.utcnow().date()}] Deadline pushed: {reason}"
        ws.update_cell(row, COL_NOTES + 1, f"{existing_note}; {note_entry}".lstrip("; "))
    return f"Updated '{task_name}' deadline to {new_deadline}."


def add_task(task_name: str, deadline: str, priority: str, category: str) -> str:
    ws = _get_sheet(config.SHEET_CHECKLIST_TAB)
    # Columns A–F: completed, task, notes, deadline, priority, category
    ws.append_row(["No", task_name, "", deadline, priority, category])
    return f"Added task '{task_name}' (due {deadline}, {priority}, {category})."


def remove_task(task_name: str) -> str:
    ws, row = _find_task_row(task_name)
    if ws is None:
        return f"Task '{task_name}' not found in the sheet."
    ws.delete_rows(row)
    return f"Removed '{task_name}' from the sheet."


def write_monthly_actual(category: str, amount: float) -> str:
    """Add to the Actual column for a category in the Monthly Budget tab."""
    try:
        ws = _get_sheet(config.SHEET_MONTHLY_BUDGET_TAB)
        all_rows = ws.get_all_values()
        for i, row in enumerate(all_rows[1:], start=2):  # 1-based, skip header
            row = _pad(row, 3)
            if row[0].strip().lower() == category.lower():
                try:
                    current = float(str(row[2]).replace(",", "").replace("$", "")) if row[2] else 0.0
                except ValueError:
                    current = 0.0
                ws.update_cell(i, 3, round(current + amount, 2))
                return f"Updated {category} actuals: +${amount:.2f}"
        return f"Category '{category}' not found in budget tab."
    except Exception as e:
        return f"Sheet write error: {e}"


def write_month_totals(totals: dict) -> str:
    """Overwrite Actual column for each matching category in the Monthly Budget tab."""
    try:
        ws = _get_sheet(config.SHEET_MONTHLY_BUDGET_TAB)
        all_rows = ws.get_all_values()
        updated = []
        for i, row in enumerate(all_rows[1:], start=2):  # 1-based, skip header
            row = _pad(row, 3)
            cat_key = row[0].strip().lower().replace(" ", "_")
            if cat_key in totals:
                ws.update_cell(i, 3, round(totals[cat_key], 2))
                updated.append(cat_key)
        return f"Wrote actuals for: {', '.join(updated)}" if updated else "No matching categories found."
    except Exception as e:
        return f"Sheet write error: {e}"


def refresh_cache():
    """Invalidate the cached spreadsheet handle so the next call re-fetches."""
    global _spreadsheet
    _spreadsheet = None
