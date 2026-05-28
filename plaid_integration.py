from __future__ import annotations
"""Plaid integration — Link flow, token exchange, daily transaction sync."""
from datetime import date, timedelta
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


# ── Access token storage ────────────────────────────────────────────────────

def store_access_token(access_token: str, item_id: str):
    database.set_context("plaid_access_token", access_token)
    database.set_context("plaid_item_id", item_id)


def get_access_token() -> str | None:
    return database.get_context("plaid_access_token")


def has_connected_account() -> bool:
    return bool(get_access_token())


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
    """Exchange public token for access token, persist it. Returns (access_token, item_id)."""
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest

    req = ItemPublicTokenExchangeRequest(public_token=public_token)
    response = _client().item_public_token_exchange(req)
    access_token = response["access_token"]
    item_id = response["item_id"]
    store_access_token(access_token, item_id)
    return access_token, item_id


# ── Transaction fetching ────────────────────────────────────────────────────

def fetch_transactions(days: int = 7) -> list[dict]:
    """Fetch recent transactions from the connected account. Returns normalized dicts."""
    access_token = get_access_token()
    if not access_token:
        return []

    from plaid.model.transactions_get_request import TransactionsGetRequest
    from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

    end = date.today()
    start = end - timedelta(days=days)

    req = TransactionsGetRequest(
        access_token=access_token,
        start_date=start,
        end_date=end,
        options=TransactionsGetRequestOptions(count=100),
    )
    response = _client().transactions_get(req)

    results = []
    for t in response["transactions"]:
        # Plaid amounts: positive = debit (money out). Negative = credit. Skip credits.
        amount = float(t["amount"])
        if amount <= 0:
            continue
        results.append({
            "transaction_id": t["transaction_id"],
            "amount": amount,
            "name": t["name"],
            "date": str(t["date"]),
            # personal_finance_category is the modern field; fall back to legacy category list
            "plaid_category": (
                t.get("personal_finance_category", {}).get("primary", "")
                or (t.get("category") or [""])[0]
            ),
        })
    return results


# ── Daily sync ──────────────────────────────────────────────────────────────

def sync_and_log_transactions(days: int = 2) -> tuple[int, list[str], list[dict]]:
    """
    Pull recent transactions, apply merchant mapping rules, log eligible ones.
    Returns (new_count, alert_messages, needs_clarification).
    """
    import categorization
    from datetime import datetime as _dt

    if not has_connected_account():
        return 0, [], []

    try:
        transactions = fetch_transactions(days=days)
    except Exception as e:
        print(f"Plaid sync error: {e}")
        return 0, [], []

    # Load all mappings once for the whole sync batch
    all_mappings = database.get_all_merchant_mappings()

    new_count = 0
    alerts = []
    needs_clarification = []

    for t in transactions:
        txn_id = t["transaction_id"]
        amount = t["amount"]
        name = t["name"]

        if database.is_transaction_logged(txn_id):
            continue

        normalized = categorization.normalize_merchant(name)

        # ── Merchant mapping lookup (fuzzy) ─────────────────────────────
        merchant_row = categorization.lookup_merchant(normalized, all_mappings)

        # Large Apple Cash payments are always ignored (rent/transfers)
        if categorization.detect_ambiguous_payment(name) == "Apple Cash" and amount > 500:
            continue

        # Explicit ignore rule
        if merchant_row and merchant_row.get("ignore"):
            continue

        # ── Determine category ──────────────────────────────────────────
        if merchant_row:
            category = merchant_row["category"]
            confidence = 95
        else:
            memory_cat = database.get_merchant_mapping(normalized)
            category, confidence = categorization.score_transaction(
                name, t["plaid_category"], amount, memory_cat
            )

        # ── Log the transaction ─────────────────────────────────────────
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

        # ── Flag-always or threshold alert ──────────────────────────────
        if merchant_row and categorization.should_flag(merchant_row, amount):
            note = merchant_row.get("note", "")
            alerts.append(categorization.build_flag_alert(name, amount, category, note))
            continue  # skip budget % alert for flagged transactions

        # ── Ambiguous P2P payment — ask Ava ────────────────────────────
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

        # ── Low-confidence → ask Ava ────────────────────────────────────
        if confidence < categorization.CONFIDENCE_THRESHOLD:
            needs_clarification.append({**t, "suggested_category": category, "confidence": confidence})
        else:
            # Persist merchant memory for high-confidence hits from keyword scoring
            if not merchant_row:
                database.set_merchant_mapping(normalized, category)
            # Budget threshold alert
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


def get_connected_accounts() -> list[dict]:
    """Return [{institution_name, accounts: [{name, mask}]}] for all stored tokens."""
    from plaid.model.accounts_get_request import AccountsGetRequest
    from plaid.model.item_get_request import ItemGetRequest
    from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
    from plaid.model.country_code import CountryCode

    access_token = get_access_token()
    if not access_token:
        return []

    client = _client()

    item_resp = client.item_get(ItemGetRequest(access_token=access_token))
    institution_id = item_resp["item"]["institution_id"]

    inst_resp = client.institutions_get_by_id(
        InstitutionsGetByIdRequest(
            institution_id=institution_id,
            country_codes=[CountryCode("US")],
        )
    )
    institution_name = inst_resp["institution"]["name"]

    accts_resp = client.accounts_get(AccountsGetRequest(access_token=access_token))
    accounts = [
        {"name": a["name"], "mask": a.get("mask") or "????"}
        for a in accts_resp["accounts"]
    ]

    return [{"institution_name": institution_name, "accounts": accounts}]


def format_connected_accounts(institutions: list[dict]) -> str:
    if not institutions:
        return "No accounts connected yet — text 'connect Plaid' to add one."
    lines = ["Connected accounts:"]
    for inst in institutions:
        parts = [f"{a['name']} (...{a['mask']})" for a in inst["accounts"]]
        lines.append(f"{inst['institution_name']} — {', '.join(parts)}")
    return "\n".join(lines)


def is_connected() -> bool:
    return PLAID_AVAILABLE and has_connected_account()
