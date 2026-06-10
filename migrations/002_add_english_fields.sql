alter table contacts
  add column if not exists english_name text;

alter table companies
  add column if not exists english_name text;

alter table addresses
  add column if not exists english_address text;

create index if not exists contacts_english_name_idx on contacts (english_name);
create index if not exists companies_english_name_idx on companies (english_name);
