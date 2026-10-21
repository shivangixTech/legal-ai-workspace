-- Legal AI Workspace — Supabase schema (Day 1)
-- Run this in Supabase Dashboard → SQL Editor → New Query → Run
-- pgvector embedding size = 768 (Gemini text-embedding-004)

-- 1. Enable pgvector
create extension if not exists "vector";

-- 2. Organizations (firm / workspace tenant)
create table if not exists organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz default now()
);

-- 3. Users (mapped from Google OAuth)
create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  name text,
  picture text,
  google_subject text unique,
  created_at timestamptz default now(),
  last_login timestamptz
);

-- 4. Organization members (RBAC)
create table if not exists organization_members (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references organizations(id) on delete cascade,
  user_id uuid references users(id) on delete cascade,
  role text not null check (role in ('owner','admin','lawyer','paralegal','viewer')),
  created_at timestamptz default now(),
  unique (organization_id, user_id)
);

-- 5. Cases (main legal workspace boundary — everything is case-scoped)
create table if not exists cases (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references organizations(id) on delete cascade,
  title text not null,
  description text,
  status text default 'open' check (status in ('open','closed','archived')),
  created_by uuid references users(id),
  created_at timestamptz default now()
);

-- 6. Case members (fine-grained access)
create table if not exists case_members (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references cases(id) on delete cascade,
  user_id uuid references users(id) on delete cascade,
  permission text not null check (permission in ('owner','editor','viewer')),
  created_at timestamptz default now(),
  unique (case_id, user_id)
);

-- 7. Documents (logical record; file lives in Supabase Storage)
create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references cases(id) on delete cascade,
  title text not null,
  storage_key text not null,          -- path inside Supabase Storage bucket
  mime_type text,
  size_bytes bigint,
  upload_status text default 'uploaded' check (upload_status in ('uploaded','parsing','chunking','embedding','indexed','failed')),
  uploaded_by uuid references users(id),
  created_at timestamptz default now()
);

-- 8. Document versions (immutable; re-upload of same hash = no duplicate work)
create table if not exists document_versions (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id) on delete cascade,
  content_hash text not null,
  page_count int,
  parser_status text default 'pending',
  created_at timestamptz default now()
);

-- 9. Document chunks (hybrid retrieval index: vector + full-text)
create table if not exists document_chunks (
  id uuid primary key default gen_random_uuid(),
  version_id uuid references document_versions(id) on delete cascade,
  document_id uuid references documents(id) on delete cascade,
  case_id uuid references cases(id) on delete cascade,
  page_number int,
  chunk_index int,
  text text not null,
  embedding vector(768),              -- Gemini text-embedding-004
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);
create index if not exists idx_chunks_case on document_chunks (case_id);
create index if not exists idx_chunks_embedding on document_chunks
  using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- 10. Conversations (chat threads, always inside a case)
create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references cases(id) on delete cascade,
  user_id uuid references users(id),
  title text,
  created_at timestamptz default now()
);

-- 11. Messages (immutable log)
create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid references conversations(id) on delete cascade,
  role text not null check (role in ('user','assistant','system')),
  content text not null,
  citations jsonb default '[]'::jsonb,   -- [{document_id, page, chunk_id, quote}]
  model_metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);
create index if not exists idx_messages_conv on messages (conversation_id, created_at);

-- 12. Case memories (long-term, selective — not every message)
create table if not exists case_memories (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references cases(id) on delete cascade,
  text text not null,
  embedding vector(768),
  importance text default 'medium' check (importance in ('low','medium','high')),
  source text,                        -- e.g. 'chat:2026-10-15' provenance
  created_at timestamptz default now()
);

-- 13. Research runs (LangGraph agent executions)
create table if not exists research_runs (
  id uuid primary key default gen_random_uuid(),
  case_id uuid references cases(id) on delete cascade,
  user_id uuid references users(id),
  query text not null,
  plan jsonb default '{}'::jsonb,
  status text default 'running' check (status in ('running','completed','failed','stopped')),
  sources jsonb default '[]'::jsonb,
  report text,
  cost_usd numeric(10,4) default 0,
  created_at timestamptz default now()
);

-- 14. Audit logs (append-only)
create table if not exists audit_logs (
  id uuid primary key default gen_random_uuid(),
  actor_id uuid references users(id),
  action text not null,
  object_type text,
  object_id uuid,
  ip_hash text,
  created_at timestamptz default now()
);

-- 15. Feedback (eval dataset seed)
create table if not exists feedback (
  id uuid primary key default gen_random_uuid(),
  message_id uuid references messages(id) on delete cascade,
  user_id uuid references users(id),
  rating int check (rating in (-1, 1)),
  reason text,
  created_at timestamptz default now()
);
