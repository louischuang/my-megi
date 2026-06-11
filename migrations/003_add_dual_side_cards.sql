alter table business_cards
  add column if not exists back_original_filename text,
  add column if not exists back_storage_path text,
  add column if not exists back_mime_type text,
  add column if not exists back_file_size_bytes bigint,
  add column if not exists back_checksum_sha256 text,
  add column if not exists side_metadata jsonb not null default '{}'::jsonb,
  add column if not exists extra_notes text;

alter table contacts
  add column if not exists extra_notes text;
