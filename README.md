# KitchenOS

**Collaborative household OS** — sync your Google Calendar, plan meals, and keep a
smart grocery inventory that updates itself when you eat.

> Phase 1 (this branch): project scaffolding, library skeleton, and SQL schema.
> No auth flows, UI polish, or Edge Functions yet — that's Phase 2.

---

## Tech stack

| Layer         | Choice                                               |
|---------------|------------------------------------------------------|
| Frontend      | Next.js 15 (App Router, RSC), Tailwind, Shadcn/UI    |
| State / data  | Supabase JS (SSR cookies), server actions             |
| Database      | Supabase Postgres with row-level security             |
| Auth          | Supabase Auth (email + Google OAuth for Calendar)    |
| Integrations  | Google Calendar API (`googleapis`), Anthropic Claude  |
| Hosting       | Vercel (web) + Supabase Edge Functions (scheduled)   |

---

## Directory layout

```
Foody/
├── app/                        # Next.js App Router (mobile-first)
│   ├── (auth)/                 # login, signup - no chrome
│   ├── (app)/                  # authenticated app shell with bottom nav
│   │   ├── dashboard/
│   │   ├── inventory/
│   │   ├── meals/
│   │   ├── shopping-list/
│   │   └── calendar/
│   ├── layout.tsx              # root layout (viewport, theme)
│   └── page.tsx                # marketing / landing
├── components/
│   ├── ui/                     # Shadcn/UI primitives (added via CLI)
│   └── shared/                 # app-wide components (e.g. MobileNav)
├── lib/                        # API clients & pure helpers
│   ├── env.ts                  # zod-validated env loader (server/public split)
│   ├── utils.ts                # `cn()` and friends
│   ├── supabase/
│   │   ├── client.ts           # browser client
│   │   ├── server.ts           # RSC + admin (service role) clients
│   │   └── middleware.ts       # cookie refresh for auth
│   ├── google/
│   │   └── calendar.ts         # OAuth + events.list
│   └── llm/
│       ├── client.ts           # Anthropic SDK singleton
│       └── meal-reasoning.ts   # meal → ingredient deductions
├── supabase/
│   └── migrations/
│       └── 0001_init.sql       # schema + RLS + confirm_meal_eaten()
├── types/
│   └── database.ts             # regenerated via `npm run db:types`
├── middleware.ts               # delegates to lib/supabase/middleware.ts
├── components.json             # Shadcn config
└── .env.example
```

**Mobile-first.** The app shell is a single-column layout capped at `max-w-2xl`
with a fixed bottom-nav (`components/shared/mobile-nav.tsx`). `viewportFit:
"cover"` + `env(safe-area-inset-bottom)` handle iOS notches.

---

## Data model

All domain rows are scoped by `household_id`; RLS policies grant access only to
`household_members`. The core tables:

| Table                  | Purpose                                                              |
|------------------------|----------------------------------------------------------------------|
| `users`                | Profile shadow of `auth.users` (email, display name, avatar)         |
| `households`           | The collaboration unit                                               |
| `household_members`    | M2M join, with `owner` / `member` roles                              |
| `inventory_items`      | Pantry stock with quantity, unit, expiration, low-threshold          |
| `shopping_list_items`  | Pending/purchased items with `store_prices` jsonb for comparison     |
| `meals`                | Planned vs. eaten meals, linked to household + creator               |
| `meal_ingredients`     | Per-meal ingredient rows, optionally linked to `inventory_items`     |
| `calendar_events`      | Cached Google Calendar events (idempotent by `(provider, external_id)`) |
| `oauth_tokens`         | Per-user, per-provider tokens (self-only RLS)                        |

See `supabase/migrations/0001_init.sql` for the full definition including enums,
indexes, triggers, and policies.

---

## Smart Inventory Deduction

The central automation: **confirming you ate a meal updates your pantry**.

### Flow

1. **Plan.** User adds a meal (`meals.status = 'planned'`) with a freeform
   description. The LLM (`lib/llm/meal-reasoning.ts`) is asked to produce a
   normalized list of `{ name, quantity, unit, confidence }` deductions.
2. **Match.** For each inferred ingredient, the server tries to match it to an
   existing `inventory_items` row in the household (case-insensitive name +
   unit match; fuzzy fallback planned). A matched row's `id` is stored on
   `meal_ingredients.inventory_item_id`; unmatched ingredients still get a
   `meal_ingredients` row (for cooking lists) but can't auto-deduct.
3. **Confirm.** When the user taps "I ate this", the client calls the Postgres
   RPC `confirm_meal_eaten(p_meal_id)`. That function, running as a single
   transaction under the caller's auth context, will:
   - flip `meals.status` to `'eaten'` and stamp `eaten_at`,
   - subtract `meal_ingredients.quantity` from each linked
     `inventory_items.quantity` (clamped at 0), and
   - mark the ingredient rows `deducted = true` so a retry is idempotent.
4. **Replenish.** A background job (Supabase Edge Function, Phase 3) scans for
   items at/below `low_threshold` and inserts `shopping_list_items`.

### Why an RPC, not a trigger?

- We want a single atomic call from the client; triggers would spread the logic
  across multiple statements and make it harder to preview the deduction
  before committing.
- The RPC is `security invoker`, so RLS still applies — a user can never
  confirm a meal in a household they don't belong to.
- Unit conversion is intentionally **not** done in SQL. The current RPC only
  deducts when `meal_ingredients.unit = inventory_items.unit`. Conversions
  (e.g. `tbsp` → `g` for flour) need ingredient-specific density tables and
  belong in the app layer, where they can be audited and overridden.

### Edge cases handled

- **Undo.** If a user re-opens a meal and flips it back to `planned`, a sibling
  RPC (Phase 2) will re-add the deducted quantities using `deducted = true` as
  the undo log.
- **Partial pantry.** Ingredients without a matched `inventory_item_id` are
  ignored by the deduction — they still appear on the cooking checklist.
- **Zero clamp.** `greatest(0, quantity - mi.quantity)` prevents negative stock
  when estimates overshoot; the LLM confidence score will drive a future
  "review before deducting" prompt.

---

## Getting started (Phase 1)

```bash
# 1. Install (run locally; this branch only scaffolds files)
npm install

# 2. Copy env
cp .env.example .env.local
# fill in Supabase + Google OAuth + Anthropic keys

# 3. Apply schema
#    Using Supabase CLI against a linked project:
supabase db push
#    Or paste supabase/migrations/0001_init.sql into the SQL editor.

# 4. Regenerate types
npm run db:types

# 5. Dev
npm run dev
```

---

## Roadmap

- **Phase 1 (this branch):** scaffolding, schema, API-client skeletons. ✅
- **Phase 2:** Supabase Auth flow, household onboarding, inventory CRUD UI,
  Google OAuth connect + initial calendar sync, meal planner UI calling
  `inferDeductions` + `confirm_meal_eaten`.
- **Phase 3:** Edge Functions for scheduled Calendar sync and low-stock
  shopping-list generation, price-comparison ingestion, push notifications.
- **Phase 4:** household analytics (waste tracking, per-meal cost), recipe
  library, barcode scanning on mobile.
