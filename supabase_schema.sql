-- Run this in your Supabase SQL editor to create all required tables.

-- Conversation history (all modes share this)
create table if not exists conversation_history (
    id bigint generated always as identity primary key,
    role text not null,           -- 'user' | 'assistant' | 'system'
    content text not null,
    mode text default 'general',  -- 'financial' | 'checklist' | 'life_coach' | 'general' | 'scheduled' | 'setup'
    from_number text,             -- sender's WhatsApp number (for user messages)
    created_at timestamptz default now()
);

create index if not exists idx_conversation_created on conversation_history(created_at desc);

-- Spending logs (manual + Plaid)
create table if not exists spending_logs (
    id bigint generated always as identity primary key,
    amount numeric(10,2) not null,
    category text not null,
    description text,
    date date not null,
    source text default 'manual',  -- 'manual' | 'plaid'
    external_id text,              -- Plaid transaction_id for dedup
    created_at timestamptz default now()
);

create index if not exists idx_spending_date on spending_logs(date desc);
create index if not exists idx_spending_category on spending_logs(category);
create unique index if not exists idx_spending_external_id on spending_logs(external_id)
    where external_id is not null;

-- If you already ran the schema and need to add the column to an existing table:
-- alter table spending_logs add column if not exists external_id text;
-- create unique index if not exists idx_spending_external_id on spending_logs(external_id) where external_id is not null;

-- Merchant category memory (keyed on normalized merchant name)
create table if not exists merchant_mappings (
    id bigint generated always as identity primary key,
    merchant_pattern text unique not null,  -- normalized (lowercase, stripped) merchant name
    category text not null,
    first_seen timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_merchant_pattern on merchant_mappings(merchant_pattern);

-- If adding to an existing database:
-- (no ALTER needed — this is a new table)

alter table merchant_mappings enable row level security;
create policy "Service role full access" on merchant_mappings for all using (true);

-- Dynamic context (Layer 2 law school + misc persistent state)
create table if not exists dynamic_context (
    id bigint generated always as identity primary key,
    key text unique not null,
    value text not null,           -- JSON-encoded
    updated_at timestamptz default now()
);

-- Weekly check-in memory
create table if not exists checkin_responses (
    id bigint generated always as identity primary key,
    week_key text unique not null,  -- e.g. "2026-W33"
    notes text not null,
    created_at timestamptz default now()
);

-- Enable Row Level Security (optional — safe defaults)
alter table conversation_history enable row level security;
alter table spending_logs enable row level security;
alter table dynamic_context enable row level security;
alter table checkin_responses enable row level security;

-- Allow service role full access (your SUPABASE_KEY should be the service role key)
create policy "Service role full access" on conversation_history for all using (true);
create policy "Service role full access" on spending_logs for all using (true);
create policy "Service role full access" on dynamic_context for all using (true);
create policy "Service role full access" on checkin_responses for all using (true);
