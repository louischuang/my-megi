create table if not exists api_access_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  name text not null default 'Default API Token',
  token_hash text not null unique,
  token_prefix text not null,
  status text not null default 'active',
  last_used_at timestamptz,
  expires_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint api_access_tokens_status_check check (status in ('active', 'expired', 'revoked'))
);

create unique index if not exists api_access_tokens_one_active_per_user_idx
  on api_access_tokens (user_id)
  where status = 'active';

create index if not exists api_access_tokens_user_id_idx on api_access_tokens (user_id);
create index if not exists api_access_tokens_token_hash_idx on api_access_tokens (token_hash);
create index if not exists api_access_tokens_created_at_idx on api_access_tokens (created_at desc);
