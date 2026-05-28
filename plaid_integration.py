from __future__ import annotations
"""Plaid integration — Link flow, token exchange, daily transaction sync."""
from datetime import date
import config
import database

PLAID_AVAILABLE = bool(config.PLAID_CLIENT_ID and config.PLAID_SECRET)


# ── Client factory ──────────────────────────────────────────────────────────

def _client():
    if not PLAID_AVAILABLE:
        raise RuntimeError("Plaid credentials not configured.")
    import plaid
    from plaid.api import plaid_api

    host = plaid.Environment.Sandbox if config.PLAID_ENV == "sandbox" else plaid.Environment.Production
    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": config.PLAID_CLIENT_ID,
            "secret": config.PLAID_SECRET,
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


# ── Account status ──────────────────────────────────────────────────────────

def has_connected_account() -> bool:
    return database.has_any_plaid_token()


def is_connected() -> bool:
    return PLAID_AVAILABLE and has_connected_account()


# ── Link token creation ─────────────────────────────────────────────────────

def create_link_token() -> str:
    """Create a Plaid Link token. Returns the link_token string."""
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.products import Products
    from plaid.model.country_code import CountryCode

    req = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="Life Coach Agent",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="ava"),
    )
    response = _client().link_token_create(req)
    return response["link_token"]


# ── Public token exchange ───────────────────────────────────────────────────

def exchange_and_store(public_token: str) -> tuple[str, str]:
    """
    Exchange public token for access token.
    Fetches institution info and stores a new row in plaid_tokens (keyed by item_id).
    Returns (access_token, item_id).
    """
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    from plaid.model.item_get_request import ItemGetRequest
    from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
    from plaid.model.country_code import CountryCode

    client = _client()

    req = ItemPublicTokenExchangeRequest(public_token=public_token)
    response = client.item_public_token_exchange(req)
    access_token = response["access_token"]
    item_id = response["item_id"]

    # Fetch institution info to store alongside the token
    institution_name = ""
    institution_id = ""
    try:
        item_resp = client.item_get(ItemGetRequest(access_token=access_token))
        institution_id = item_resp["item"]["institution_id"] or ""
        if institution_id:
            inst_resp = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=institution_id,
                    country_codes=[CountryCode("US")],
                )
            )
            institution_name = inst_resp["institution"]["name"]
    except Exception as e:
        print(f"Could not fetch institution info: {e}")

    database.store_plaid_token(access_token, item_id, institution_name, institution_id)
    return access_token, item_id


# ── Transaction fetching ────────────────────────────────────────────────────

def _fetch_transactions_for_token(
    access_token: str,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch transactions for a single access token between two ISO date strings."""
    from plaid.model.transactions_get_request import TransactionsGetRequest
    from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

    start = date.fromisoformat(start_date)
    end   = date.fromisoformat(end_date)

    req = TransactionsGetRequest(
        access_token=access_token,
        start_date=start,
        end_date=end,
        options=TransactionsGetRequestOptions(count=500),
    )
    response = _client().transactions_get(req)

    results = []
    for t in response["transactions"]:
        amount = float(t["amount"])
        if amount <= 0:
            continue
        results.append({
            "transaction_id": t["transaction_id"],
            "amount": amount,
            "name": t["name"],
            "date": str(t["date"]),
            "plaid_category": (
                t.get("personal_finance_category", {}).get("primary", "")
                or (t.get("category") or [""])[0]
            ),
        })
    return results


def fetch_transactions(start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """Fetch transactions from ALL connected institutions, combined."""
    today = date.today()
    start_date = start_date or today.replace(day=1).isoformat()
    end_date   = end_date   or today.isoformat()

    tokens = database.get_all_plaid_tokens()
    if not tokens:
        return []

    all_txns = []
    for row in tokens:
        try:
            all_txns.extend(_fetch_transactions_for_token(row["access_token"], start_date, end_date))
        except Exception as e:
            print(f"Plaid fetch error for {row.get('institution_name', row['item_id'])}: {e}")
    return all_txns


# ── Daily sync ──────────────────────────────────────────────────────────────

def sync_and_log_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int | None = None,  # kept for backward compat with scheduler calls — ignored
) -> tuple[int, list[str], list[dict]]:
    """
    Pull transactions from all connected institutions for the current calendar month
    (or an explicit start/end range), apply merchant mapping rules, log eligible ones.
    Returns (new_count, alert_messages, needs_clarification).
    """
    import categorization
    from datetime import datetime as _dt

    today = date.today()
    start_date = start_date or today.replace(day=1).isoformat()
    end_date   = end_date   or today.isoformat()

    tokens = database.get_all_plaid_tokens()
    if not tokens:
        return 0, [], []

    # Load all mappings once for the whole sync batch
    all_mappings = database.get_all_merchant_mappings()

    new_count = 0
    alerts = []
    needs_clarification = []

    for token_row in tokens:
        inst_name = token_row.get("institution_name") or token_row["item_id"]
        try:
            transactions = _fetch_transactions_for_token(
                token_row["access_token"], start_date, end_date
            )
        except Exception as e:
            print(f"Plaid sync error for {inst_name}: {e}")
            continue

        for t in transactions:
            txn_id = t["transaction_id"]
            amount = t["amount"]
            name = t["name"]

            if database.is_transaction_logged(txn_id):
                continue

            normalized = categorization.normalize_merchant(name)

            merchant_row = categorization.lookup_merchant(normalized, all_mappings)

            # Large Apple Cash payments are always ignored (rent/transfers)
            if categorization.detect_ambiguous_payment(name) == "Apple Cash" and amount > 500:
                continue

            # Explicit ignore rule
            if merchant_row and merchant_row.get("ignore"):
                continue

            # Determine category
            if merchant_row:
                category = merchant_row["category"]
                confidence = 95
            else:
                memory_cat = database.get_merchant_mapping(normalized)
                category, confidence = categorization.score_transaction(
                    name, t["plaid_category"], amount, memory_cat
                )

            database.log_spend(
                amount=amount,
                category=category,
                description=name,
                date=t["date"],
                source="plaid",
                external_id=txn_id,
            )
            database.set_last_plaid_transaction({**t, "logged_category": category})
            new_count += 1

            # Flag-always or threshold alert
            if merchant_row and categorization.should_flag(merchant_row, amount):
                note = merchant_row.get("note", "")
                alerts.append(categorization.build_flag_alert(name, amount, category, note))
                continue

            # Ambiguous P2P payment — ask Ava
            payment_type = categorization.detect_ambiguous_payment(name)
            if payment_type and not merchant_row:
                needs_clarification.append({
                    **t,
                    "suggested_category": category,
                    "confidence": confidence,
                    "clarification_type": "ambiguous",
                    "payment_type": payment_type,
                })
                continue

            # Low-confidence → ask Ava
            if confidence < categorization.CONFIDENCE_THRESHOLD:
                needs_clarification.append({**t, "suggested_category": category, "confidence": confidence})
            else:
                if not merchant_row:
                    database.set_merchant_mapping(normalized, category)
                budget = config.BUDGET_TARGETS.get(category, 0)
                if budget:
                    total = database.get_category_spend_this_month(category)
                    pct = total / budget * 100
                    if pct >= 75 and _dt.utcnow().day < 20:
                        alerts.append(
                            f"Alert: {category.replace('_', ' ').title()} hit {pct:.0f}% "
                            f"(${total:.0f}/${budget}) after a ${amount:.0f} charge at {name}."
                        )

    return new_count, alerts, needs_clarification


# ── Connected accounts ──────────────────────────────────────────────────────

def get_connected_accounts() -> list[dict]:
    """
    Return [{institution_name, accounts: [{name, mask}]}] for all stored tokens.
    Institution name comes from the stored row; accounts are fetched from Plaid.
    """
    from plaid.model.accounts_get_request import AccountsGetRequest

    tokens = database.get_all_plaid_tokens()
    if not tokens:
        return []

    client = _client()
    result = []
    for row in tokens:
        try:
            accts_resp = client.accounts_get(AccountsGetRequest(access_token=row["access_token"]))
            accounts = [
                {"name": a["name"], "mask": a.get("mask") or "????"}
                for a in accts_resp["accounts"]
            ]
            result.append({
                "institution_name": row.get("institution_name") or "Unknown Bank",
                "accounts": accounts,
            })
        except Exception as e:
            print(f"accounts_get error for {row.get('institution_name', row['item_id'])}: {e}")
    return result


def backfill_transactions(start_date: str = "2026-01-01") -> tuple[int, list[str], list[dict]]:
    """Pull all transactions from start_date through today and log any not yet stored."""
    end_date = date.today().isoformat()
    print(f"[BACKFILL] {start_date} → {end_date}")
    return sync_and_log_transactions(start_date=start_date, end_date=end_date)


def format_connected_accounts(institutions: list[dict]) -> str:
    if not institutions:
        return "No accounts connected yet — text 'connect Plaid' to add one."
    lines = ["Connected accounts:"]
    for inst in institutions:
        parts = [f"{a['name']} (...{a['mask']})" for a in inst["accounts"]]
        lines.append(f"{inst['institution_name']} — {', '.join(parts)}")
    return "\n".join(lines)
