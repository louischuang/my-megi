create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email citext not null unique,
  display_name text not null,
  password_hash text not null,
  status text not null default 'active',
  metadata jsonb not null default '{}'::jsonb,
  last_login_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz,
  constraint users_status_check check (status in ('active', 'disabled'))
);

create index if not exists users_status_idx on users (status);
create index if not exists users_created_at_idx on users (created_at desc);

create table if not exists roles (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  name text not null,
  description text,
  created_at timestamptz not null default now()
);

insert into roles (code, name, description) values
  ('system_admin', 'System Administrator', 'Can manage users only.'),
  ('content_admin', 'Content Administrator', 'Can view and manage all business cards and contacts.'),
  ('user', 'User', 'Can view and manage owned business cards and contacts only.')
on conflict (code) do update
set name = excluded.name,
    description = excluded.description;

create table if not exists user_roles (
  user_id uuid not null references users(id) on delete cascade,
  role_id uuid not null references roles(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, role_id)
);

create index if not exists user_roles_role_id_idx on user_roles (role_id);

create table if not exists auth_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  session_token_hash text not null unique,
  user_agent text,
  ip_address inet,
  expires_at timestamptz not null,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists auth_sessions_user_id_idx on auth_sessions (user_id);
create index if not exists auth_sessions_expires_at_idx on auth_sessions (expires_at);

alter table business_cards
  add column if not exists owner_user_id uuid references users(id) on delete restrict;

alter table contacts
  add column if not exists owner_user_id uuid references users(id) on delete restrict;

alter table relationship_notes
  add column if not exists owner_user_id uuid references users(id) on delete restrict;

create index if not exists business_cards_owner_user_id_idx on business_cards (owner_user_id);
create index if not exists contacts_owner_user_id_idx on contacts (owner_user_id);
create index if not exists relationship_notes_owner_user_id_idx on relationship_notes (owner_user_id);
