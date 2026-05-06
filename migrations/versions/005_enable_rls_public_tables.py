"""Enable Row-Level Security on all public-schema tables

Revision ID: 005
Revises: 004
Create Date: 2026-04-30

Supabase flagged "Table publicly accessible" because RLS was disabled
on every public-schema table. The Supabase anon/authenticated JWT keys
go through PostgREST, which respects RLS — without it, anyone holding
the anon key could read or modify every row.

Our backend talks to Postgres directly via SQLAlchemy as the postgres
role (table owner), which bypasses RLS. So enabling RLS without any
policies is the correct one-shot fix: it locks out anon/authenticated
PostgREST access while leaving our cron jobs and webhook function
fully functional.

If we ever ship a client-side Supabase integration, we'll add per-table
policies in a follow-up migration. Until then: deny by default.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every table currently in the public schema (22 in total). Sourced from
# Supabase list_tables advisory; sorted into rough domain groups for
# readability. alembic_version is included so the advisory clears
# completely — the postgres role owns it and bypasses RLS, so this does
# not interfere with future migrations.
_RLS_TABLES: tuple[str, ...] = (
    # Alembic internals
    "alembic_version",
    # Users & auth
    "users",
    "user_profiles",
    "user_integrations",
    # Calendar
    "calendar_events",
    # Meal plans & meals
    "meal_plans",
    "meals",
    "meal_logs",
    "leftovers",
    # Clarification system (legacy interactive flow)
    "clarification_sessions",
    "clarification_questions",
    # Memory & observability
    "agent_memories",
    "meal_history_index",
    "agent_runs",
    # Recipes & ingredients
    "ingredients",
    "recipes",
    "recipe_ingredients",
    # Phase 2 household stubs
    "households",
    "household_members",
    "inventory_items",
    "grocery_lists",
    "grocery_list_items",
)


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY;')


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY;')
