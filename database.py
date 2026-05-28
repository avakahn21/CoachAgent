from __future__ import annotations
import json
from datetime import datetime
from supabase import create_client
import config

_client = None


def get_client():
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


def init_tables():
    """Ensure required tables exist via Supabase SQL."""
    # Tables are created via Supabase dashboard or migrations.
    # This is a no-op placeholder — see README for schema SQL.
    pass


# ── Conversation history ────────────────────────────────────────────────────

def save_message(role: str, content: str, mode: str = "general"):
    db = get_client()
    db.table("conversation_history").insert({
        "role": role,
        "content": content,
        "mode": mode,
        "created_at": datetime.utcnow().isoformat(),
    }).execute()


def get_recent_history(limit: int = 20) -> list[dict]:
    db = get_client()
    res = (
        db.table("conversation_history")
        .select("role,content,mode,created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(res.data))


# ── Spending logs ───────────────────────────────────────────────────────────

def log_spend(
    amount: float,
    category: str,
    description: str,
    date: str = None,
    source: str = "manual",
    external_id: str = None,
):
    db = get_client()
    row = {
        "amount": amount,
        "category": category,
        "description": description,
        "date": date or datetime.utcnow().date().isoformat(),
        "source": source,
        "created_at": datetime.utcnow().isoformat(),
    }
    if external_id:
        row["external_id"] = external_id
    db.table("spending_logs").insert(row).execute()


def is_transaction_logged(external_id: str) -> bool:
    """Return True if a Plaid transaction_id has already been logged."""
    db = get_client()
    res = (
        db.table("spending_logs")
        .select("id")
        .eq("external_id", external_id)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def get_monthly_spend(year: int, month: int) -> list[dict]:
    db = get_client()
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"
    res = (
        db.table("spending_logs")
        .select("*")
        .gte("date", start)
        .lt("date", end)
        .execute()
    )
    return res.data


def get_category_spend_this_month(category: str) -> float:
    now = datetime.utcnow()
    rows = get_monthly_spend(now.year, now.month)
    return sum(r["amount"] for r in rows if r["category"] == category)


def get_all_category_totals_this_month() -> dict[str, float]:
    now = datetime.utcnow()
    rows = get_monthly_spend(now.year, now.month)
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["category"]] = totals.get(r["category"], 0) + r["amount"]
    return totals


# ── Dynamic context (Layer 2 law school) ───────────────────────────────────

def set_context(key: str, value):
    db = get_client()
    existing = (
        db.table("dynamic_context")
        .select("id")
        .eq("key", key)
        .execute()
    )
    payload = {"key": key, "value": json.dumps(value), "updated_at": datetime.utcnow().isoformat()}
    if existing.data:
        db.table("dynamic_context").update(payload).eq("key", key).execute()
    else:
        db.table("dynamic_context").insert(payload).execute()


def get_context(key: str):
    db = get_client()
    res = db.table("dynamic_context").select("value").eq("key", key).execute()
    if res.data:
        return json.loads(res.data[0]["value"])
    return None


def get_all_context() -> dict:
    db = get_client()
    res = db.table("dynamic_context").select("key,value").execute()
    return {r["key"]: json.loads(r["value"]) for r in res.data}


# ── Weekly check-in memory ──────────────────────────────────────────────────

def save_checkin(week_key: str, notes: str):
    db = get_client()
    db.table("checkin_responses").upsert({
        "week_key": week_key,
        "notes": notes,
        "created_at": datetime.utcnow().isoformat(),
    }).execute()


def get_checkin(week_key: str) -> str | None:
    db = get_client()
    res = db.table("checkin_responses").select("notes").eq("week_key", week_key).execute()
    return res.data[0]["notes"] if res.data else None


def get_recent_checkins(limit: int = 4) -> list[dict]:
    db = get_client()
    res = (
        db.table("checkin_responses")
        .select("week_key,notes,created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


# ── Merchant mappings ───────────────────────────────────────────────────────

def get_merchant_mapping(normalized_name: str) -> str | None:
    db = get_client()
    res = (
        db.table("merchant_mappings")
        .select("category")
        .eq("merchant_pattern", normalized_name)
        .limit(1)
        .execute()
    )
    return res.data[0]["category"] if res.data else None


def get_all_merchant_mappings() -> list[dict]:
    """Return all rows from merchant_mappings for fuzzy matching."""
    db = get_client()
    res = db.table("merchant_mappings").select("*").execute()
    return res.data or []


def set_merchant_mapping(normalized_name: str, category: str):
    db = get_client()
    now = datetime.utcnow().isoformat()
    existing = (
        db.table("merchant_mappings")
        .select("id")
        .eq("merchant_pattern", normalized_name)
        .execute()
    )
    if existing.data:
        db.table("merchant_mappings").update({
            "category": category, "updated_at": now,
        }).eq("merchant_pattern", normalized_name).execute()
    else:
        db.table("merchant_mappings").insert({
            "merchant_pattern": normalized_name,
            "category": category,
            "first_seen": now,
            "updated_at": now,
        }).execute()


def upsert_merchant_mapping(
    pattern: str,
    category: str,
    ignore: bool = False,
    flag_always: bool = False,
    flag_threshold: float = 0,
    note: str = "",
):
    """Insert or update a full merchant mapping row (used by seeder and user corrections)."""
    db = get_client()
    now = datetime.utcnow().isoformat()
    existing = (
        db.table("merchant_mappings")
        .select("id")
        .eq("merchant_pattern", pattern)
        .execute()
    )
    payload = {
        "merchant_pattern": pattern,
        "category": category,
        "ignore": ignore,
        "flag_always": flag_always,
        "flag_threshold": flag_threshold,
        "note": note,
        "updated_at": now,
    }
    if existing.data:
        db.table("merchant_mappings").update(payload).eq("merchant_pattern", pattern).execute()
    else:
        payload["first_seen"] = now
        db.table("merchant_mappings").insert(payload).execute()


def count_merchant_transactions_this_month(description_pattern: str) -> int:
    """Count how many times a merchant appears in this month's spending logs."""
    now = datetime.utcnow()
    rows = get_monthly_spend(now.year, now.month)
    return sum(1 for r in rows if description_pattern.lower() in r.get("description", "").lower())


# ── Clarification queue ─────────────────────────────────────────────────────

def get_current_clarification() -> dict | None:
    return get_context("clarification_current")


def set_current_clarification(txn: dict | None):
    set_context("clarification_current", txn)


def push_clarification_queue(txn: dict):
    queue = get_context("clarification_queue") or []
    queue.append(txn)
    set_context("clarification_queue", queue)


def pop_clarification_queue() -> dict | None:
    queue = get_context("clarification_queue") or []
    if not queue:
        return None
    nxt = queue.pop(0)
    set_context("clarification_queue", queue)
    return nxt


# ── Transaction updates ─────────────────────────────────────────────────────

def recategorize_transaction(external_id: str, new_category: str):
    db = get_client()
    db.table("spending_logs").update(
        {"category": new_category}
    ).eq("external_id", external_id).execute()


def delete_transaction(external_id: str):
    db = get_client()
    db.table("spending_logs").delete().eq("external_id", external_id).execute()


def get_last_plaid_transaction() -> dict | None:
    return get_context("last_plaid_transaction")


def set_last_plaid_transaction(txn: dict):
    set_context("last_plaid_transaction", txn)
