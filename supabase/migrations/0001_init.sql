-- ============================================================================
--  KitchenOS - initial schema
--  PostgreSQL / Supabase
--
--  Design principles:
--   * Households are the unit of collaboration. Every domain row is scoped by
--     household_id; RLS policies check membership via household_members.
--   * app.users is a lightweight profile table that shadows auth.users (1:1).
--   * Units are stored as ENUMs; conversions live in app code, not triggers.
--   * Smart Inventory Deduction is implemented in app code / Edge Functions:
--     confirming a meal inserts rows into meal_ingredients and then updates
--     inventory_items.quantity in a single transaction. See README.md.
-- ============================================================================

create extension if not exists "pgcrypto";
create extension if not exists "citext";

-- ---------------------------------------------------------------------------
--  Enums
-- ---------------------------------------------------------------------------
create type household_role as enum ('owner', 'member');

create type measurement_unit as enum (
  'pcs', 'g', 'kg', 'ml', 'l', 'tbsp', 'tsp', 'cup'
);

create type meal_type as enum ('breakfast', 'lunch', 'dinner', 'snack');

create type meal_status as enum ('planned', 'eaten', 'skipped');

create type shopping_item_status as enum ('pending', 'purchased', 'cancelled');

create type calendar_provider as enum ('google');

-- ---------------------------------------------------------------------------
--  Users (profile shadow of auth.users)
-- ---------------------------------------------------------------------------
create table public.users (
  id            uuid primary key references auth.users (id) on delete cascade,
  email         citext not null unique,
  display_name  text,
  avatar_url    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
--  Households & membership
-- ---------------------------------------------------------------------------
create table public.households (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  created_by  uuid not null references public.users (id) on delete restrict,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.household_members (
  household_id uuid not null references public.households (id) on delete cascade,
  user_id      uuid not null references public.users (id) on delete cascade,
  role         household_role not null default 'member',
  joined_at    timestamptz not null default now(),
  primary key (household_id, user_id)
);

create index on public.household_members (user_id);

-- Helper: is the caller a member of this household?
create or replace function public.is_household_member(h uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists(
    select 1 from public.household_members
    where household_id = h and user_id = auth.uid()
  );
$$;

-- ---------------------------------------------------------------------------
--  Inventory
-- ---------------------------------------------------------------------------
create table public.inventory_items (
  id             uuid primary key default gen_random_uuid(),
  household_id   uuid not null references public.households (id) on delete cascade,
  name           text not null,
  category       text,
  quantity       numeric(10, 2) not null default 0 check (quantity >= 0),
  unit           measurement_unit not null default 'pcs',
  low_threshold  numeric(10, 2),                 -- triggers shopping suggestions
  expires_at     date,                           -- null = non-perishable
  notes          text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index on public.inventory_items (household_id);
create index on public.inventory_items (household_id, expires_at);
create index on public.inventory_items (household_id, lower(name));

-- ---------------------------------------------------------------------------
--  Shopping list
--    * inventory_item_id is optional: list may include items not yet in pantry.
--    * store_prices is a jsonb blob of { "store_name": price } for comparison.
-- ---------------------------------------------------------------------------
create table public.shopping_list_items (
  id                  uuid primary key default gen_random_uuid(),
  household_id        uuid not null references public.households (id) on delete cascade,
  inventory_item_id   uuid references public.inventory_items (id) on delete set null,
  name                text not null,
  quantity            numeric(10, 2) not null default 1 check (quantity > 0),
  unit                measurement_unit not null default 'pcs',
  status              shopping_item_status not null default 'pending',
  store_prices        jsonb not null default '{}'::jsonb,
  preferred_store     text,
  added_by            uuid references public.users (id) on delete set null,
  purchased_at        timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create index on public.shopping_list_items (household_id, status);

-- ---------------------------------------------------------------------------
--  Meals + ingredients
--    * meals.status drives Smart Inventory Deduction:
--        'planned' -> no pantry effect
--        'eaten'   -> deduct meal_ingredients from inventory
--        'skipped' -> no effect (and reverses deduction if previously eaten)
--    * meal_ingredients.inventory_item_id nullable to allow freeform entries
--      that the LLM couldn't match to a pantry row.
-- ---------------------------------------------------------------------------
create table public.meals (
  id              uuid primary key default gen_random_uuid(),
  household_id    uuid not null references public.households (id) on delete cascade,
  title           text not null,
  meal_type       meal_type not null default 'dinner',
  servings        int not null default 2 check (servings > 0),
  planned_for     timestamptz not null,
  status          meal_status not null default 'planned',
  eaten_at        timestamptz,
  notes           text,
  created_by      uuid references public.users (id) on delete set null,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index on public.meals (household_id, planned_for);
create index on public.meals (household_id, status);

create table public.meal_ingredients (
  id                 uuid primary key default gen_random_uuid(),
  meal_id            uuid not null references public.meals (id) on delete cascade,
  inventory_item_id  uuid references public.inventory_items (id) on delete set null,
  name               text not null,
  quantity           numeric(10, 2) not null check (quantity > 0),
  unit               measurement_unit not null,
  deducted           boolean not null default false,    -- flipped true when meal marked 'eaten'
  created_at         timestamptz not null default now()
);

create index on public.meal_ingredients (meal_id);
create index on public.meal_ingredients (inventory_item_id);

-- ---------------------------------------------------------------------------
--  Calendar events (cache of external provider events)
--    * (provider, external_id) is globally unique so re-syncs are idempotent.
-- ---------------------------------------------------------------------------
create table public.calendar_events (
  id            uuid primary key default gen_random_uuid(),
  household_id  uuid not null references public.households (id) on delete cascade,
  user_id       uuid not null references public.users (id) on delete cascade,
  provider      calendar_provider not null default 'google',
  external_id   text not null,
  calendar_id   text not null,
  title         text,
  description   text,
  location      text,
  starts_at     timestamptz not null,
  ends_at       timestamptz not null,
  all_day       boolean not null default false,
  raw           jsonb,
  synced_at     timestamptz not null default now(),
  unique (provider, external_id)
);

create index on public.calendar_events (household_id, starts_at);
create index on public.calendar_events (user_id, starts_at);

-- ---------------------------------------------------------------------------
--  Third-party OAuth tokens (server-only)
-- ---------------------------------------------------------------------------
create table public.oauth_tokens (
  user_id        uuid not null references public.users (id) on delete cascade,
  provider       calendar_provider not null,
  access_token   text not null,
  refresh_token  text,
  scope          text,
  expires_at     timestamptz,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  primary key (user_id, provider)
);

-- ---------------------------------------------------------------------------
--  updated_at triggers
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

do $$
declare
  t text;
begin
  for t in select unnest(array[
    'users',
    'households',
    'inventory_items',
    'shopping_list_items',
    'meals',
    'oauth_tokens'
  ])
  loop
    execute format(
      'create trigger trg_%I_updated_at
         before update on public.%I
         for each row execute function public.set_updated_at();',
      t, t
    );
  end loop;
end$$;

-- ---------------------------------------------------------------------------
--  Row Level Security
--    All domain tables are household-scoped; access is granted via membership.
-- ---------------------------------------------------------------------------
alter table public.users               enable row level security;
alter table public.households          enable row level security;
alter table public.household_members   enable row level security;
alter table public.inventory_items     enable row level security;
alter table public.shopping_list_items enable row level security;
alter table public.meals               enable row level security;
alter table public.meal_ingredients    enable row level security;
alter table public.calendar_events     enable row level security;
alter table public.oauth_tokens        enable row level security;

-- users: each user sees/edits their own profile
create policy "users self-read"
  on public.users for select using (id = auth.uid());
create policy "users self-update"
  on public.users for update using (id = auth.uid());
create policy "users self-insert"
  on public.users for insert with check (id = auth.uid());

-- households: visible to members; only owners mutate metadata
create policy "household read"
  on public.households for select
  using (public.is_household_member(id));
create policy "household insert by creator"
  on public.households for insert
  with check (created_by = auth.uid());
create policy "household update by owner"
  on public.households for update
  using (exists (
    select 1 from public.household_members m
    where m.household_id = households.id
      and m.user_id = auth.uid()
      and m.role = 'owner'
  ));

-- household_members: members can see their roster; owners manage it
create policy "members read"
  on public.household_members for select
  using (public.is_household_member(household_id));
create policy "members insert by owner"
  on public.household_members for insert
  with check (exists (
    select 1 from public.household_members m
    where m.household_id = household_members.household_id
      and m.user_id = auth.uid()
      and m.role = 'owner'
  ));
create policy "members delete by owner or self"
  on public.household_members for delete
  using (
    user_id = auth.uid()
    or exists (
      select 1 from public.household_members m
      where m.household_id = household_members.household_id
        and m.user_id = auth.uid()
        and m.role = 'owner'
    )
  );

-- Generic household-member policy template for domain tables
create policy "inventory rw by member"
  on public.inventory_items for all
  using (public.is_household_member(household_id))
  with check (public.is_household_member(household_id));

create policy "shopping rw by member"
  on public.shopping_list_items for all
  using (public.is_household_member(household_id))
  with check (public.is_household_member(household_id));

create policy "meals rw by member"
  on public.meals for all
  using (public.is_household_member(household_id))
  with check (public.is_household_member(household_id));

create policy "meal ingredients rw by member"
  on public.meal_ingredients for all
  using (exists (
    select 1 from public.meals m
    where m.id = meal_ingredients.meal_id
      and public.is_household_member(m.household_id)
  ))
  with check (exists (
    select 1 from public.meals m
    where m.id = meal_ingredients.meal_id
      and public.is_household_member(m.household_id)
  ));

create policy "calendar rw by member"
  on public.calendar_events for all
  using (public.is_household_member(household_id))
  with check (public.is_household_member(household_id));

-- OAuth tokens: strictly self-only; app code uses service role for refresh.
create policy "oauth self-read"
  on public.oauth_tokens for select using (user_id = auth.uid());
create policy "oauth self-write"
  on public.oauth_tokens for all using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- ---------------------------------------------------------------------------
--  Smart Inventory Deduction: atomic RPC
--    Clients call this when flipping a meal to 'eaten'. It:
--      1. marks the meal as eaten,
--      2. deducts each meal_ingredients row linked to an inventory_item,
--      3. marks those ingredients as deducted = true.
--    Everything runs in a single transaction under the caller's auth context;
--    RLS still applies, so cross-household abuse is impossible.
-- ---------------------------------------------------------------------------
create or replace function public.confirm_meal_eaten(p_meal_id uuid)
returns void
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_household uuid;
begin
  select household_id into v_household from public.meals where id = p_meal_id;
  if v_household is null then
    raise exception 'meal % not found', p_meal_id;
  end if;

  update public.meals
     set status = 'eaten', eaten_at = now()
   where id = p_meal_id
     and status <> 'eaten';

  update public.inventory_items inv
     set quantity = greatest(0, inv.quantity - mi.quantity)
    from public.meal_ingredients mi
   where mi.meal_id = p_meal_id
     and mi.inventory_item_id = inv.id
     and mi.deducted = false
     and mi.unit = inv.unit;       -- unit conversion handled in app layer

  update public.meal_ingredients
     set deducted = true
   where meal_id = p_meal_id
     and inventory_item_id is not null;
end;
$$;

comment on function public.confirm_meal_eaten(uuid) is
  'Smart Inventory Deduction: marks a meal eaten and deducts linked ingredients from inventory in one transaction.';
