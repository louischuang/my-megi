create extension if not exists pgcrypto;
create extension if not exists citext;

create table companies (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  normalized_name text not null,
  tax_id text,
  website text,
  industry text,
  company_type text,
  english_name text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index companies_normalized_name_idx on companies (normalized_name);
create index companies_industry_idx on companies (industry);

create table contacts (
  id uuid primary key default gen_random_uuid(),
  company_id uuid references companies(id) on delete set null,
  display_name text not null,
  english_name text,
  given_name text,
  family_name text,
  nickname text,
  title text,
  department text,
  preferred_language text,
  notes text,
  extra_notes text,
  source_business_card_id uuid,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);

create index contacts_display_name_idx on contacts (display_name);
create index contacts_company_id_idx on contacts (company_id);
create index contacts_created_at_idx on contacts (created_at desc);
create index contacts_metadata_gin_idx on contacts using gin (metadata);

create table business_cards (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid references contacts(id) on delete set null,
  original_filename text not null,
  storage_path text not null,
  mime_type text not null,
  file_size_bytes bigint not null,
  checksum_sha256 text,
  back_original_filename text,
  back_storage_path text,
  back_mime_type text,
  back_file_size_bytes bigint,
  back_checksum_sha256 text,
  status text not null default 'pending',
  ocr_engine text,
  ocr_text text,
  ocr_metadata jsonb not null default '{}'::jsonb,
  side_metadata jsonb not null default '{}'::jsonb,
  llm_provider text,
  llm_model text,
  llm_raw_output jsonb not null default '{}'::jsonb,
  extracted_data jsonb not null default '{}'::jsonb,
  extraction_confidence numeric(5, 4),
  extra_notes text,
  error_code text,
  error_message text,
  processed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint business_cards_status_check check (
    status in ('pending', 'processing', 'completed', 'failed', 'needs_review')
  )
);

alter table contacts
  add constraint contacts_source_business_card_id_fkey
  foreign key (source_business_card_id) references business_cards(id) on delete set null;

create index business_cards_contact_id_idx on business_cards (contact_id);
create index business_cards_status_idx on business_cards (status);
create index business_cards_created_at_idx on business_cards (created_at desc);
create index business_cards_extracted_data_gin_idx on business_cards using gin (extracted_data);

create table contact_methods (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid not null references contacts(id) on delete cascade,
  method_type text not null,
  label text,
  value text not null,
  normalized_value citext not null,
  is_primary boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint contact_methods_type_check check (
    method_type in ('email', 'phone', 'mobile', 'website', 'linkedin', 'line', 'wechat', 'other')
  )
);

create index contact_methods_contact_id_idx on contact_methods (contact_id);
create index contact_methods_type_value_idx on contact_methods (method_type, normalized_value);

create table addresses (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid references contacts(id) on delete cascade,
  company_id uuid references companies(id) on delete cascade,
  label text,
  country text,
  region text,
  city text,
  district text,
  postal_code text,
  raw_address text not null,
  english_address text,
  normalized_address text,
  latitude numeric(10, 7),
  longitude numeric(10, 7),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint addresses_owner_check check (
    contact_id is not null or company_id is not null
  )
);

create index addresses_contact_id_idx on addresses (contact_id);
create index addresses_company_id_idx on addresses (company_id);
create index addresses_region_city_idx on addresses (country, region, city);

create table relationship_notes (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid not null references contacts(id) on delete cascade,
  business_card_id uuid references business_cards(id) on delete set null,
  met_at text,
  met_on date,
  introduced_by text,
  summary text,
  next_action text,
  next_action_due_on date,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index relationship_notes_contact_id_idx on relationship_notes (contact_id);
create index relationship_notes_met_on_idx on relationship_notes (met_on desc);

create table classification_types (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  name text not null,
  created_at timestamptz not null default now()
);

insert into classification_types (code, name) values
  ('company', 'Company Classification'),
  ('region', 'Region Classification'),
  ('industry', 'Industry Classification')
on conflict (code) do nothing;

create table classifications (
  id uuid primary key default gen_random_uuid(),
  type_id uuid not null references classification_types(id) on delete cascade,
  parent_id uuid references classifications(id) on delete set null,
  code text,
  name text not null,
  normalized_name text not null,
  description text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index classifications_type_name_idx on classifications (type_id, normalized_name);
create index classifications_parent_id_idx on classifications (parent_id);

create table contact_classifications (
  contact_id uuid not null references contacts(id) on delete cascade,
  classification_id uuid not null references classifications(id) on delete cascade,
  source text not null default 'manual',
  confidence numeric(5, 4),
  created_at timestamptz not null default now(),
  primary key (contact_id, classification_id),
  constraint contact_classifications_source_check check (
    source in ('manual', 'llm', 'rule', 'import')
  )
);

create index contact_classifications_classification_id_idx
  on contact_classifications (classification_id);

create table tags (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  normalized_name text not null unique,
  color text,
  created_at timestamptz not null default now()
);

create table contact_tags (
  contact_id uuid not null references contacts(id) on delete cascade,
  tag_id uuid not null references tags(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (contact_id, tag_id)
);

create index contact_tags_tag_id_idx on contact_tags (tag_id);

create table audit_logs (
  id uuid primary key default gen_random_uuid(),
  actor_type text not null default 'user',
  actor_id text,
  action text not null,
  entity_type text not null,
  entity_id uuid,
  before_data jsonb,
  after_data jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index audit_logs_entity_idx on audit_logs (entity_type, entity_id);
create index audit_logs_created_at_idx on audit_logs (created_at desc);
