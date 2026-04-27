"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-27

Creates all Phase 1 tables plus Phase 2 household/inventory stubs.
Table creation order respects all foreign key dependencies.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # gen_random_uuid()
    # pgvector is enabled but the embedding column is added in migration 002
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # Tier 0: no foreign keys
    # ------------------------------------------------------------------

    op.create_table(
        "users",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text()),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Jerusalem"),
        sa.Column("locale", sa.String(16), nullable=False, server_default="he-IL"),
        sa.Column("telegram_chat_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "households",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "ingredients",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64)),
        sa.Column("default_unit", sa.String(16), nullable=False),
        sa.Column("kcal_per_100", sa.Numeric(7, 2)),
        sa.Column("protein_per_100", sa.Numeric(6, 2)),
        sa.Column("carbs_per_100", sa.Numeric(6, 2)),
        sa.Column("fat_per_100", sa.Numeric(6, 2)),
        sa.UniqueConstraint("canonical_name", name="uq_ingredients_canonical_name"),
    )

    # ------------------------------------------------------------------
    # Tier 1: FK → users / households / ingredients
    # ------------------------------------------------------------------

    op.create_table(
        "user_profiles",
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("date_of_birth", sa.Date()),
        sa.Column("sex", sa.String(16)),
        sa.Column("height_cm", sa.Numeric(5, 2)),
        sa.Column("weight_kg", sa.Numeric(5, 2)),
        sa.Column("activity_level", sa.String(32)),
        sa.Column("goal", sa.String(32)),
        sa.Column("target_calories", sa.Integer()),
        sa.Column("target_protein_g", sa.Integer()),
        sa.Column("target_carbs_g", sa.Integer()),
        sa.Column("target_fat_g", sa.Integer()),
        sa.Column("dietary_pattern", sa.String(64)),
        sa.Column("allergies", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dislikes", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("favorites", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("cooking_skill", sa.String(32)),
        sa.Column("kitchen_equipment", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("notes", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "user_integrations",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("access_token", sa.Text()),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_integrations_user_provider"),
    )

    op.create_table(
        "calendar_events",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="google_calendar"),
        sa.Column("title", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_category", sa.String(32)),
        sa.Column("intensity", sa.String(16)),
        sa.Column("is_at_home", sa.Boolean()),
        sa.Column("needs_clarification", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True)),
        sa.Column("classifier_version", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "provider", "external_id", name="uq_calendar_events_user_provider_ext"),
    )
    op.create_index("idx_cal_events_user_day", "calendar_events", ["user_id", "starts_at"])

    op.create_table(
        "meal_plans",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("summary", sa.Text()),
        sa.Column("total_kcal", sa.Integer()),
        sa.Column("total_protein_g", sa.Integer()),
        sa.Column("total_carbs_g", sa.Integer()),
        sa.Column("total_fat_g", sa.Integer()),
        sa.Column("assumptions_made", sa.Text()),
        sa.Column("reasoning", JSONB),
        sa.Column("model_version", sa.String(64)),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "plan_date", name="uq_meal_plans_user_date"),
    )

    op.create_table(
        "clarification_sessions",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("telegram_message_id", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "plan_date", name="uq_clarification_sessions_user_date"),
    )

    op.create_table(
        "agent_memories",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default=sa.text("0.80")),
        sa.Column("source", sa.Text()),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        # embedding vector(1536) added in migration 002
    )
    op.create_index("idx_agent_mem_user_kind", "agent_memories", ["user_id", "kind"])

    op.create_table(
        "meal_history_index",
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("meal_signature", sa.Text(), primary_key=True),
        sa.Column("last_suggested", sa.Date(), nullable=False),
        sa.Column("times_30d", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )

    op.create_table(
        "recipes",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text()),
        sa.Column("prep_minutes", sa.Integer()),
        sa.Column("cook_minutes", sa.Integer()),
        sa.Column("servings", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("tags", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "household_members",
        sa.Column("household_id", PG_UUID(as_uuid=True), sa.ForeignKey("households.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
    )

    op.create_table(
        "inventory_items",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("household_id", PG_UUID(as_uuid=True), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", PG_UUID(as_uuid=True), sa.ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Numeric(8, 2), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("expires_on", sa.Date()),
        sa.Column("location", sa.String(32)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "grocery_lists",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("household_id", PG_UUID(as_uuid=True), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ------------------------------------------------------------------
    # Tier 2: FK → meal_plans / clarification_sessions / recipes / etc.
    # ------------------------------------------------------------------

    op.create_table(
        "meals",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("meal_plan_id", PG_UUID(as_uuid=True), sa.ForeignKey("meal_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slot", sa.String(32), nullable=False),
        sa.Column("suggested_time", sa.Time()),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("kcal", sa.Integer()),
        sa.Column("protein_g", sa.Integer()),
        sa.Column("carbs_g", sa.Integer()),
        sa.Column("fat_g", sa.Integer()),
        sa.Column("rationale", sa.Text()),
        sa.Column("context_tags", ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("sequence", sa.Integer(), nullable=False),
    )
    op.create_index("idx_meals_plan_seq", "meals", ["meal_plan_id", "sequence"])

    op.create_table(
        "agent_runs",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_plan_id", PG_UUID(as_uuid=True), sa.ForeignKey("meal_plans.id", ondelete="SET NULL")),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64)),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(8, 4)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "clarification_questions",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", PG_UUID(as_uuid=True), sa.ForeignKey("clarification_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(64), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=False, server_default="yes_no"),
        sa.Column("options", JSONB),
        sa.Column("context", JSONB),
        sa.Column("answer", sa.Text()),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("assumption", sa.Text()),
        sa.Column("sequence", sa.Integer(), nullable=False),
    )

    op.create_table(
        "recipe_ingredients",
        sa.Column("recipe_id", PG_UUID(as_uuid=True), sa.ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ingredient_id", PG_UUID(as_uuid=True), sa.ForeignKey("ingredients.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("quantity", sa.Numeric(8, 2), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("is_optional", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "grocery_list_items",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("grocery_list_id", PG_UUID(as_uuid=True), sa.ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", PG_UUID(as_uuid=True), sa.ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Numeric(8, 2)),
        sa.Column("unit", sa.String(32)),
        sa.Column("purchased", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ------------------------------------------------------------------
    # Tier 3: FK → meals
    # ------------------------------------------------------------------

    op.create_table(
        "meal_logs",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meal_id", PG_UUID(as_uuid=True), sa.ForeignKey("meals.id", ondelete="SET NULL")),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_meal_logs_rating")),
        sa.Column("substitution", sa.Text()),
        sa.Column("notes", sa.Text()),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("meal_logs")
    op.drop_table("grocery_list_items")
    op.drop_table("recipe_ingredients")
    op.drop_table("clarification_questions")
    op.drop_index("idx_meals_plan_seq", table_name="meals")
    op.drop_table("agent_runs")
    op.drop_table("meals")
    op.drop_table("grocery_lists")
    op.drop_table("inventory_items")
    op.drop_table("household_members")
    op.drop_table("recipes")
    op.drop_index("idx_agent_mem_user_kind", table_name="agent_memories")
    op.drop_table("meal_history_index")
    op.drop_table("agent_memories")
    op.drop_table("clarification_sessions")
    op.drop_table("meal_plans")
    op.drop_index("idx_cal_events_user_day", table_name="calendar_events")
    op.drop_table("calendar_events")
    op.drop_table("user_integrations")
    op.drop_table("user_profiles")
    op.drop_table("households")
    op.drop_table("ingredients")
    op.drop_table("users")
