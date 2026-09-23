create type offer_status as enum
  ('collected', 'prefiltered', 'classified', 'enriched', 'notified', 'rejected');
create type user_status as enum
  ('new', 'interested', 'applied', 'interview', 'offer', 'rejected', 'dismissed');

create table offers (
  id uuid primary key default gen_random_uuid(),
  dedup_key text not null unique,
  title text not null,
  company text,
  country text,
  city text,
  description text not null default '',
  description_is_full boolean not null default false,
  posted_at timestamptz,
  collected_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  status offer_status not null default 'collected',
  rejected_stage text,
  rejected_reason text,
  decisions jsonb not null default '{}',
  extracted jsonb not null default '{}',
  flags jsonb not null default '[]',
  score numeric,
  score_breakdown jsonb not null default '{}',
  summary text,
  engine_version text,
  notified_at timestamptz,
  updated_at timestamptz not null default now()
);
create index offers_status_idx on offers (status);

create table offer_sources (
  offer_id uuid not null references offers (id) on delete cascade,
  source text not null,
  source_id text not null,
  url text not null,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  raw jsonb not null default '{}',
  primary key (source, source_id)
);
create index offer_sources_offer_idx on offer_sources (offer_id);

create table applications (
  offer_id uuid primary key references offers (id) on delete cascade,
  user_status user_status not null default 'new',
  notes text,
  label boolean,
  updated_at timestamptz not null default now()
);

create table runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  counts jsonb not null default '{}',
  errors jsonb not null default '{}'
);

create function create_application() returns trigger
language plpgsql as $$
begin
  insert into applications (offer_id) values (new.id) on conflict do nothing;
  return new;
end $$;

create trigger offers_create_application
  after update of status on offers
  for each row
  when (new.status = 'classified' and old.status is distinct from 'classified')
  execute function create_application();

create function touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create trigger applications_touch before update on applications
  for each row execute function touch_updated_at();

alter table offers enable row level security;
alter table offer_sources enable row level security;
alter table applications enable row level security;
alter table runs enable row level security;

create view v_inbox with (security_invoker = true) as
select a.user_status, o.score, o.title, o.company, o.country, o.city, o.summary, o.flags,
       (o.extracted ->> 'salary_eur_month')::int as salary_eur_month, o.posted_at,
       (select string_agg(s.url, ' ') from offer_sources s where s.offer_id = o.id) as urls,
       o.id
from offers o
join applications a on a.offer_id = o.id
where a.user_status in ('new', 'interested') and o.status <> 'rejected'
order by o.score desc nulls last;

create view v_tracking with (security_invoker = true) as
select a.user_status, a.notes, a.updated_at, o.title, o.company, o.country, o.city,
       (select string_agg(s.url, ' ') from offer_sources s where s.offer_id = o.id) as urls,
       o.id
from offers o
join applications a on a.offer_id = o.id
where a.user_status in ('applied', 'interview', 'offer')
order by a.updated_at desc;

create view v_rejected_recent with (security_invoker = true) as
select o.updated_at, o.rejected_stage, o.rejected_reason, o.title, o.company, o.country, o.id
from offers o
where o.status = 'rejected' and o.updated_at > now() - interval '7 days'
order by o.updated_at desc;
