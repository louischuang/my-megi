with ranked_source_contacts as (
  select
    id,
    source_business_card_id,
    row_number() over (
      partition by source_business_card_id
      order by updated_at desc, created_at desc, id desc
    ) as rank
  from contacts
  where source_business_card_id is not null
    and deleted_at is null
),
selected_source_contacts as (
  select id, source_business_card_id
  from ranked_source_contacts
  where rank = 1
)
update business_cards as card
set contact_id = selected.id,
    updated_at = now()
from selected_source_contacts as selected
where card.id = selected.source_business_card_id
  and card.contact_id is null;

with ranked_source_contacts as (
  select
    id,
    source_business_card_id,
    row_number() over (
      partition by source_business_card_id
      order by updated_at desc, created_at desc, id desc
    ) as rank
  from contacts
  where source_business_card_id is not null
    and deleted_at is null
)
update contacts as contact
set metadata = contact.metadata || jsonb_build_object(
      'unlinkedDuplicateSourceBusinessCardId',
      ranked.source_business_card_id::text
    ),
    source_business_card_id = null,
    updated_at = now()
from ranked_source_contacts as ranked
where contact.id = ranked.id
  and ranked.rank > 1;

create unique index if not exists contacts_source_business_card_id_unique_idx
  on contacts (source_business_card_id)
  where source_business_card_id is not null
    and deleted_at is null;
