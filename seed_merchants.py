#!/usr/bin/env python3
"""
Seed merchant_mappings table with Ava's known merchants from Rocket Money history.
Run once (or re-run to update): python seed_merchants.py

Prerequisites:
  1. Run migrations/add_merchant_fields.sql in Supabase SQL Editor first.
  2. .env must be present with SUPABASE_URL and SUPABASE_KEY.
"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import database

# Each dict: pattern (lowercase core identifier), category, ignore, flag_always, flag_threshold, note
# Patterns use word-set matching — "uber eats" matches "UBER *EATS", "Uber Eats", etc.
MERCHANT_SEED: list[dict] = [

    # ── IGNORE: income ─────────────────────────────────────────────────────
    {"pattern": "warner bros", "category": "income", "ignore": True, "note": "payroll"},
    {"pattern": "katayoun sahafi", "category": "income", "ignore": True, "note": "mom transfer"},
    {"pattern": "benefit payments", "category": "income", "ignore": True, "note": "WBD benefits/severance"},
    {"pattern": "franchise tax bd", "category": "income", "ignore": True, "note": "tax refund"},

    # ── IGNORE: transfers & payments ──────────────────────────────────────
    {"pattern": "online banking transfer", "category": "transfer", "ignore": True},
    {"pattern": "online scheduled transfer", "category": "transfer", "ignore": True},
    {"pattern": "internal transfer", "category": "transfer", "ignore": True},
    {"pattern": "chase credit crd", "category": "transfer", "ignore": True, "note": "credit card payment"},
    {"pattern": "epay", "category": "transfer", "ignore": True, "note": "credit card payment"},
    {"pattern": "payment from chk", "category": "transfer", "ignore": True},
    {"pattern": "return of posted check", "category": "transfer", "ignore": True},
    {"pattern": "paypal forward", "category": "transfer", "ignore": True},
    {"pattern": "lls web", "category": "transfer", "ignore": True},
    {"pattern": "javid tavari", "category": "transfer", "ignore": True},
    {"pattern": "forward", "category": "transfer", "ignore": True},

    # ── IGNORE: savings ────────────────────────────────────────────────────
    {"pattern": "rocket savings", "category": "savings", "ignore": True},
    {"pattern": "rocket money savings", "category": "savings", "ignore": True},
    {"pattern": "save for vacation", "category": "savings", "ignore": True},

    # ── IGNORE: investments ────────────────────────────────────────────────
    {"pattern": "acorns invest", "category": "investment", "ignore": True},
    {"pattern": "robinhood", "category": "investment", "ignore": True},
    {"pattern": "wfcs", "category": "investment", "ignore": True},
    {"pattern": "fid bkg svc", "category": "investment", "ignore": True, "note": "Fidelity"},

    # ── IGNORE: loan / rent / bills (parents cover) ────────────────────────
    {"pattern": "unitedwholesale", "category": "loan", "ignore": True, "note": "mortgage"},
    {"pattern": "tala residences", "category": "rent", "ignore": True, "note": "parents cover"},
    {"pattern": "jenkins properties", "category": "rent", "ignore": True, "note": "parents cover"},
    {"pattern": "spectrum", "category": "bills", "ignore": True, "note": "parents cover"},
    {"pattern": "so california edison", "category": "bills", "ignore": True, "note": "parents cover"},
    {"pattern": "in growth management", "category": "bills", "ignore": True},
    {"pattern": "uwm appraisal", "category": "home", "ignore": True, "note": "mortgage related — parents cover"},

    # ── DINING: delivery (flag_always) ─────────────────────────────────────
    {"pattern": "uber eats", "category": "dining", "flag_always": True, "note": "delivery"},
    {"pattern": "doordash", "category": "dining", "flag_always": True, "note": "delivery"},

    # ── DINING: restaurants ────────────────────────────────────────────────
    {"pattern": "living room", "category": "dining"},
    {"pattern": "marvito", "category": "dining"},
    {"pattern": "alba", "category": "dining"},
    {"pattern": "darling", "category": "dining"},
    {"pattern": "manpuku", "category": "dining"},
    {"pattern": "jinpachi sushi", "category": "dining"},
    {"pattern": "e baldi", "category": "dining"},
    {"pattern": "nobu", "category": "dining"},
    {"pattern": "bichi", "category": "dining"},
    {"pattern": "in-n-out", "category": "dining"},
    {"pattern": "krispy kreme", "category": "dining"},
    {"pattern": "salt and straw", "category": "dining"},
    {"pattern": "gingers divine", "category": "dining"},
    {"pattern": "bacio di latte", "category": "dining"},
    {"pattern": "prince st pizza", "category": "dining"},
    {"pattern": "lutie cafe", "category": "dining"},
    {"pattern": "earthbar", "category": "dining"},
    {"pattern": "bravo toast", "category": "dining"},
    {"pattern": "community goods", "category": "dining"},
    {"pattern": "oakberry", "category": "dining"},
    {"pattern": "go greek yogurt", "category": "dining"},
    {"pattern": "warnerbros2cafe", "category": "dining"},
    {"pattern": "sonias cafe", "category": "dining"},

    # ── GROCERIES ──────────────────────────────────────────────────────────
    {"pattern": "erewhon", "category": "groceries", "flag_always": True, "note": "expensive — flag every visit"},
    {"pattern": "wholefds", "category": "groceries"},
    {"pattern": "whole foods", "category": "groceries"},
    {"pattern": "bristol farms", "category": "groceries"},
    {"pattern": "gelsons", "category": "groceries"},
    {"pattern": "gelson", "category": "groceries"},
    {"pattern": "trader joe", "category": "groceries"},
    {"pattern": "pavilions", "category": "groceries"},
    {"pattern": "organic oren", "category": "groceries"},
    {"pattern": "instacart", "category": "groceries"},

    # ── COFFEE (sub-category of dining) ────────────────────────────────────
    {"pattern": "alfred coffee", "category": "dining", "note": "coffee"},
    {"pattern": "starbucks", "category": "dining", "note": "coffee"},
    {"pattern": "sunset oil mini mart", "category": "dining", "note": "coffee"},
    {"pattern": "tabacchi bros", "category": "dining", "note": "coffee"},
    {"pattern": "unincorporated coff", "category": "dining", "note": "coffee"},
    {"pattern": "pantry west hollywood", "category": "dining", "note": "coffee"},
    {"pattern": "verve coffee", "category": "dining", "note": "coffee"},
    {"pattern": "laurel supply", "category": "dining", "note": "coffee"},
    {"pattern": "saint frank coffee", "category": "dining", "note": "coffee"},
    {"pattern": "neighborhood 3rd st", "category": "dining", "note": "coffee"},

    # ── SHOPPING: flag_always ──────────────────────────────────────────────
    {"pattern": "ssense", "category": "shopping", "flag_always": True},
    {"pattern": "ebdenim", "category": "shopping", "flag_always": True},
    {"pattern": "onequince", "category": "shopping", "flag_always": True},
    {"pattern": "alo yoga", "category": "shopping", "flag_always": True},
    {"pattern": "temu", "category": "shopping", "flag_always": True},
    {"pattern": "zara", "category": "shopping", "flag_always": True},
    {"pattern": "leset", "category": "shopping", "flag_always": True},
    {"pattern": "toteme", "category": "shopping", "flag_always": True},
    {"pattern": "st agni", "category": "shopping", "flag_always": True},
    {"pattern": "intimissimi", "category": "shopping", "flag_always": True},
    {"pattern": "the great", "category": "shopping", "flag_always": True},
    {"pattern": "revolve", "category": "shopping", "flag_always": True},
    {"pattern": "shoprlt", "category": "shopping", "flag_always": True},
    {"pattern": "wardrobe nyc", "category": "shopping", "flag_always": True},
    {"pattern": "alla prima", "category": "shopping", "flag_always": True},
    {"pattern": "buck mason", "category": "shopping", "flag_always": True},
    {"pattern": "mytheresa", "category": "shopping", "flag_always": True},
    {"pattern": "orseund iris", "category": "shopping", "flag_always": True},
    {"pattern": "bellethelabel", "category": "shopping", "flag_always": True},
    {"pattern": "ralph lauren", "category": "shopping", "flag_always": True},
    {"pattern": "poshmark", "category": "shopping", "flag_always": True},
    {"pattern": "brandy melville", "category": "shopping", "flag_always": True},
    {"pattern": "redone", "category": "shopping", "flag_always": True},
    {"pattern": "farfetch", "category": "shopping", "flag_always": True},
    {"pattern": "ballerette", "category": "shopping", "flag_always": True},

    # ── SHOPPING: threshold-based ──────────────────────────────────────────
    {"pattern": "amazon", "category": "shopping", "flag_threshold": 50},
    {"pattern": "target", "category": "shopping", "flag_threshold": 100},

    # ── PERSONAL CARE ──────────────────────────────────────────────────────
    {"pattern": "sephora", "category": "personal_care"},
    {"pattern": "bellacures", "category": "personal_care", "note": "nails"},
    {"pattern": "hollyway cleaners", "category": "personal_care", "note": "dry cleaning"},
    {"pattern": "csc serviceworks", "category": "personal_care", "note": "laundry"},
    {"pattern": "payrange mobile", "category": "personal_care", "note": "laundry"},
    {"pattern": "calm laser center", "category": "personal_care", "note": "laser"},
    {"pattern": "new look skin center", "category": "personal_care", "note": "laser"},
    {"pattern": "joseph at salon", "category": "personal_care", "note": "hair"},
    {"pattern": "nafiseh", "category": "personal_care", "note": "eyebrows"},
    {"pattern": "ashley marks", "category": "personal_care"},
    {"pattern": "buffy", "category": "personal_care"},
    {"pattern": "sp arrae", "category": "personal_care", "note": "supplements"},
    {"pattern": "cvs", "category": "personal_care", "flag_threshold": 50},

    # ── GYM / FITNESS ──────────────────────────────────────────────────────
    {"pattern": "hot pilates", "category": "gym"},
    {"pattern": "heated room", "category": "gym"},
    {"pattern": "opvs fitness", "category": "gym"},
    {"pattern": "equinox", "category": "gym", "flag_always": True, "note": "should cancel — $299/mo"},
    {"pattern": "form sami clarke", "category": "subscriptions", "note": "fitness app"},
    {"pattern": "melissa wood health", "category": "subscriptions", "note": "fitness app"},
    {"pattern": "mwh dice", "category": "subscriptions", "note": "fitness app"},

    # ── TRANSPORT ──────────────────────────────────────────────────────────
    {"pattern": "uber trip", "category": "uber_lyft"},
    {"pattern": "ubr pending", "category": "uber_lyft"},
    {"pattern": "lyft ride", "category": "uber_lyft"},
    {"pattern": "lyft", "category": "uber_lyft"},
    {"pattern": "waymo", "category": "uber_lyft"},
    {"pattern": "uber one", "category": "subscriptions", "note": "Uber One membership"},

    # ── GAS ────────────────────────────────────────────────────────────────
    {"pattern": "chevron", "category": "gas"},
    {"pattern": "exxonmobil", "category": "gas"},
    {"pattern": "exxon", "category": "gas"},
    {"pattern": "shell", "category": "gas"},
    {"pattern": "arco", "category": "gas"},
    {"pattern": "76 platinum", "category": "gas"},
    {"pattern": "media center sinclair", "category": "gas", "note": "WBD lot gas station"},

    # ── PARKING ────────────────────────────────────────────────────────────
    {"pattern": "ace parking", "category": "uber_lyft", "note": "parking"},
    {"pattern": "laz parking", "category": "uber_lyft", "note": "parking"},
    {"pattern": "ladot meter", "category": "uber_lyft", "note": "parking"},
    {"pattern": "united valet", "category": "uber_lyft", "note": "parking"},
    {"pattern": "ush parking", "category": "uber_lyft", "note": "parking"},
    {"pattern": "crystal valet", "category": "uber_lyft", "note": "parking"},
    {"pattern": "valet services", "category": "uber_lyft", "note": "parking"},
    {"pattern": "west hollywood service", "category": "uber_lyft", "note": "parking"},
    {"pattern": "lugg", "category": "uber_lyft", "note": "moving/transport"},

    # ── SUBSCRIPTIONS ──────────────────────────────────────────────────────
    {"pattern": "netflix", "category": "subscriptions"},
    {"pattern": "spotify", "category": "subscriptions"},
    {"pattern": "apple com bill", "category": "subscriptions"},
    {"pattern": "apple.com", "category": "subscriptions"},
    {"pattern": "amazon prime", "category": "subscriptions"},
    {"pattern": "claude.ai", "category": "subscriptions"},
    {"pattern": "anthropic", "category": "subscriptions"},
    {"pattern": "openai", "category": "subscriptions"},
    {"pattern": "chatgpt", "category": "subscriptions"},
    {"pattern": "rocket money", "category": "subscriptions"},
    {"pattern": "smallpdf", "category": "subscriptions"},
    {"pattern": "imdbpro", "category": "subscriptions", "flag_always": True, "note": "ask if still needed in law school"},
    {"pattern": "ro health", "category": "subscriptions"},
    {"pattern": "wagmo", "category": "subscriptions", "flag_always": True, "note": "pet insurance — ask if keeping for law school"},

    # ── ENTERTAINMENT ──────────────────────────────────────────────────────
    {"pattern": "amc", "category": "entertainment"},
    {"pattern": "hudson theatre", "category": "entertainment"},
    {"pattern": "firebrand media", "category": "entertainment"},
    {"pattern": "crypto arena", "category": "entertainment"},
    {"pattern": "prime video", "category": "entertainment"},
    {"pattern": "amazon digital", "category": "entertainment"},
    {"pattern": "book soup", "category": "entertainment"},

    # ── MEDICAL ────────────────────────────────────────────────────────────
    {"pattern": "catalin marinescu", "category": "personal_care", "note": "medical"},
    {"pattern": "shahriar farzad", "category": "personal_care", "note": "medical"},

    # ── HOME / MOVING ──────────────────────────────────────────────────────
    {"pattern": "crate and barrel", "category": "misc", "note": "home"},
    {"pattern": "villeroy", "category": "misc", "note": "home"},
    {"pattern": "rolling greens", "category": "misc", "note": "home"},
    {"pattern": "home depot", "category": "misc", "note": "home"},
    {"pattern": "bloomingdales", "category": "shopping"},
]


def run():
    print(f"Seeding {len(MERCHANT_SEED)} merchant mappings...")
    ok = 0
    err = 0
    for m in MERCHANT_SEED:
        try:
            database.upsert_merchant_mapping(
                pattern=m["pattern"],
                category=m.get("category", "misc"),
                ignore=m.get("ignore", False),
                flag_always=m.get("flag_always", False),
                flag_threshold=m.get("flag_threshold", 0),
                note=m.get("note", ""),
            )
            ok += 1
        except Exception as e:
            print(f"  ERROR on '{m['pattern']}': {e}")
            err += 1
    print(f"Done — {ok} upserted, {err} errors.")


if __name__ == "__main__":
    run()
