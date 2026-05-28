-- Run this in Supabase SQL Editor before running seed_merchants.py
--
-- 1. Add all required columns (safe to re-run — IF NOT EXISTS prevents errors)
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS ignore BOOLEAN DEFAULT false;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS flag_always BOOLEAN DEFAULT false;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS flag_threshold NUMERIC DEFAULT 0;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS note TEXT DEFAULT '';

-- 2. Allow the app/seeder (anon key) to read and write merchant mappings
--    merchant_mappings contains no sensitive user data, so open access is fine here.
ALTER TABLE merchant_mappings DISABLE ROW LEVEL SECURITY;
