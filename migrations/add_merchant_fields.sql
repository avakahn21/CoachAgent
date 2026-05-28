-- ── merchant_mappings columns + RLS ────────────────────────────────────────
-- Run this in Supabase SQL Editor (safe to re-run)

ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS ignore BOOLEAN DEFAULT false;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS flag_always BOOLEAN DEFAULT false;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS flag_threshold NUMERIC DEFAULT 0;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS note TEXT DEFAULT '';
ALTER TABLE merchant_mappings DISABLE ROW LEVEL SECURITY;

-- ── plaid_tokens table (multi-institution support) ──────────────────────────

CREATE TABLE IF NOT EXISTS plaid_tokens (
  id SERIAL PRIMARY KEY,
  access_token TEXT NOT NULL,
  item_id TEXT NOT NULL UNIQUE,
  institution_name TEXT DEFAULT '',
  institution_id TEXT DEFAULT '',
  connected_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE plaid_tokens DISABLE ROW LEVEL SECURITY;
