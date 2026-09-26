-- Shared part-current database for kicad10-mcp.
--
-- Access model:
--   parts             approved entries; anyone (publishable key) can READ, nobody can write
--                     through the Data API. Rows are added by the project owner.
--   part_submissions  anyone can INSERT a proposal; nobody can read or change it through
--                     the Data API. The owner reviews them and promotes good ones with
--                     private.approve_submission(id).
--
-- New tables are not exposed to the Data API automatically (Supabase change of
-- 2026-04-28), so every grant below is explicit, and RLS is on for every table.

-- ---------------------------------------------------------------- approved parts
create table public.parts (
  key         text primary key
              check (char_length(key) between 1 and 128),
  match       text[] not null
              check (cardinality(match) between 1 and 32),
  entry       jsonb not null
              check (jsonb_typeof(entry) = 'object' and octet_length(entry::text) <= 20000),
  source      text not null
              check (char_length(source) between 5 and 1000),
  verified    boolean not null default true,
  approved_at timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

comment on table public.parts is
  'Approved part current data (supply / regulators / drivers) for kicad10-mcp power budgets.';

alter table public.parts enable row level security;

create policy "parts are readable by everyone"
  on public.parts for select
  to anon, authenticated
  using (true);

grant select on table public.parts to anon, authenticated;

-- ---------------------------------------------------------------- submissions
create table public.part_submissions (
  id           uuid primary key default gen_random_uuid(),
  key          text not null
               check (char_length(key) between 1 and 128),
  match        text[] not null
               check (cardinality(match) between 1 and 32),
  entry        jsonb not null
               check (jsonb_typeof(entry) = 'object' and octet_length(entry::text) <= 20000),
  source       text not null
               check (char_length(source) between 5 and 1000),
  verified     boolean not null default false,
  client       text check (char_length(client) <= 64),
  status       text not null default 'pending'
               check (status in ('pending', 'approved', 'rejected')),
  submitted_at timestamptz not null default now(),
  reviewed_at  timestamptz
);

comment on table public.part_submissions is
  'Proposed part data from kicad10-mcp users; write-only via the Data API, reviewed by the owner.';

-- The review queue is read in submission order.
create index part_submissions_pending_idx
  on public.part_submissions (submitted_at)
  where status = 'pending';

alter table public.part_submissions enable row level security;

-- Clients may only file new, pending, unreviewed proposals.
create policy "anyone can submit a pending proposal"
  on public.part_submissions for insert
  to anon, authenticated
  with check (status = 'pending' and reviewed_at is null);

-- No select/update/delete policies: submissions are invisible through the API.
grant insert on table public.part_submissions to anon, authenticated;

-- ---------------------------------------------------------------- review helper
-- Lives in an unexposed schema and is only executable by the owner.
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.approve_submission(submission_id uuid)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  s public.part_submissions%rowtype;
begin
  select * into s from public.part_submissions where id = submission_id for update;
  if not found then
    raise exception 'submission % not found', submission_id;
  end if;

  insert into public.parts (key, match, entry, source, verified)
  values (s.key, s.match, s.entry, s.source, true)
  on conflict (key) do update
    set match = excluded.match,
        entry = excluded.entry,
        source = excluded.source,
        verified = true,
        updated_at = now();

  update public.part_submissions
     set status = 'approved', reviewed_at = now()
   where id = submission_id;

  return s.key;
end;
$$;

revoke all on function private.approve_submission(uuid) from public, anon, authenticated;

-- ---------------------------------------------------------------- grants hardening
-- Projects created before the 2026-10-30 enforcement still auto-grant every privilege
-- (including TRUNCATE, which RLS does not cover) on new public tables. Reduce to
-- exactly the access model above and stop future objects being exposed implicitly.
revoke all on table public.parts, public.part_submissions from anon, authenticated;
grant select on table public.parts to anon, authenticated;
grant insert on table public.part_submissions to anon, authenticated;

alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke execute on functions from anon, authenticated, public;
