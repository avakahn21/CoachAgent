-- Run this in Supabase SQL Editor before deploying the merchant mapping update.
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS ignore BOOLEAN DEFAULT false;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS flag_always BOOLEAN DEFAULT false;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS flag_threshold NUMERIC DEFAULT 0;
ALTER TABLE merchant_mappings ADD COLUMN IF NOT EXISTS note TEXT DEFAULT '';
