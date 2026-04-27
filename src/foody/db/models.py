from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Users & Auth
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Asia/Jerusalem"
    )
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="he-IL")
    # Telegram chat ID stored at user level for direct messaging
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=sa.func.now(),
    )

    profile: Mapped[Optional[UserProfile]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    integrations: Mapped[list[UserIntegration]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    calendar_events: Mapped[list[CalendarEvent]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    meal_plans: Mapped[list[MealPlan]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    clarification_sessions: Mapped[list[ClarificationSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    agent_memories: Mapped[list[AgentMemory]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserProfile(Base):
    """Slow-changing nutritional & lifestyle profile for a user."""

    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    sex: Mapped[Optional[str]] = mapped_column(String(16))
    height_cm: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    weight_kg: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    # sedentary | light | moderate | high | athlete
    activity_level: Mapped[Optional[str]] = mapped_column(String(32))
    # cut | maintain | bulk | recomp
    goal: Mapped[Optional[str]] = mapped_column(String(32))
    target_calories: Mapped[Optional[int]] = mapped_column(Integer)
    target_protein_g: Mapped[Optional[int]] = mapped_column(Integer)
    target_carbs_g: Mapped[Optional[int]] = mapped_column(Integer)
    target_fat_g: Mapped[Optional[int]] = mapped_column(Integer)
    # omnivore | vegetarian | vegan | keto | etc.
    dietary_pattern: Mapped[Optional[str]] = mapped_column(String(64))
    allergies: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=sa.text("'{}'")
    )
    dislikes: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=sa.text("'{}'")
    )
    favorites: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=sa.text("'{}'")
    )
    # none | basic | intermediate | advanced
    cooking_skill: Mapped[Optional[str]] = mapped_column(String(32))
    kitchen_equipment: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=sa.text("'{}'")
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=sa.func.now(),
    )

    user: Mapped[User] = relationship(back_populates="profile")


class UserIntegration(Base):
    """OAuth / API credentials per provider. Tokens stored encrypted."""

    __tablename__ = "user_integrations"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # google_calendar | resend | telegram
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    access_token: Mapped[Optional[str]] = mapped_column(Text)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )

    user: Mapped[User] = relationship(back_populates="integrations")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_id"),
        Index("idx_cal_events_user_day", "user_id", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="google_calendar"
    )
    title: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())
    # work | study | workout | commute | meal | personal | unknown
    event_category: Mapped[Optional[str]] = mapped_column(String(32))
    # none | low | moderate | high (for workouts)
    intensity: Mapped[Optional[str]] = mapped_column(String(16))
    is_at_home: Mapped[Optional[bool]] = mapped_column(Boolean)
    needs_clarification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa.false()
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    classified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    classifier_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=sa.func.now(),
    )

    user: Mapped[User] = relationship(back_populates="calendar_events")


# ---------------------------------------------------------------------------
# Meal Plans & Meals
# ---------------------------------------------------------------------------


class MealPlan(Base):
    __tablename__ = "meal_plans"
    __table_args__ = (UniqueConstraint("user_id", "plan_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    # pending | generating | needs_clarification | finalized | sent | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    summary: Mapped[Optional[str]] = mapped_column(Text)
    total_kcal: Mapped[Optional[int]] = mapped_column(Integer)
    total_protein_g: Mapped[Optional[int]] = mapped_column(Integer)
    total_carbs_g: Mapped[Optional[int]] = mapped_column(Integer)
    total_fat_g: Mapped[Optional[int]] = mapped_column(Integer)
    # Populated when the agent uses best-guess fallbacks; shown in the email
    assumptions_made: Mapped[Optional[str]] = mapped_column(Text)
    reasoning: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_version: Mapped[Optional[str]] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Feedback fields — populated via Telegram rating buttons (migration 002)
    overall_rating: Mapped[Optional[int]] = mapped_column(
        SmallInteger,
        CheckConstraint("overall_rating BETWEEN 1 AND 5", name="ck_meal_plans_overall_rating"),
    )
    feedback_notes: Mapped[Optional[str]] = mapped_column(Text)
    feedback_telegram_msg_id: Mapped[Optional[int]] = mapped_column(Integer)

    user: Mapped[User] = relationship(back_populates="meal_plans")
    meals: Mapped[list[Meal]] = relationship(
        back_populates="meal_plan",
        cascade="all, delete-orphan",
        order_by="Meal.sequence",
    )


class Meal(Base):
    __tablename__ = "meals"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meal_plan_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("meal_plans.id", ondelete="CASCADE"), nullable=False
    )
    # breakfast | lunch | dinner | pre_workout | post_workout | snack
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    suggested_time: Mapped[Optional[time]] = mapped_column(Time)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    kcal: Mapped[Optional[int]] = mapped_column(Integer)
    protein_g: Mapped[Optional[int]] = mapped_column(Integer)
    carbs_g: Mapped[Optional[int]] = mapped_column(Integer)
    fat_g: Mapped[Optional[int]] = mapped_column(Integer)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    # e.g. ['post_workout', 'quick', 'outside_home']
    context_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=sa.text("'{}'")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    meal_plan: Mapped[MealPlan] = relationship(back_populates="meals")


class MealLog(Base):
    """User feedback on what they actually ate. Drives the memory loop."""

    __tablename__ = "meal_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("meals.id", ondelete="SET NULL")
    )
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rating: Mapped[Optional[int]] = mapped_column(
        SmallInteger, CheckConstraint("rating BETWEEN 1 AND 5", name="ck_meal_logs_rating")
    )
    substitution: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Clarification System — Human-in-the-Loop via Telegram
# ---------------------------------------------------------------------------


class ClarificationSession(Base):
    """One row per user per day. Groups all evening questions into a single Telegram message."""

    __tablename__ = "clarification_sessions"
    __table_args__ = (UniqueConstraint("user_id", "plan_date"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plan_date: Mapped[date] = mapped_column(Date, nullable=False)
    # The Telegram message ID of the evening digest message (for editing in-place)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer)
    # pending | partially_answered | fully_answered | expired | resolved
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Morning job expires unanswered questions and applies assumptions after this time
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )

    user: Mapped[User] = relationship(back_populates="clarification_sessions")
    questions: Mapped[list[ClarificationQuestion]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ClarificationQuestion.sequence",
    )


class ClarificationQuestion(Base):
    """A single yes/no or multiple-choice question within an evening session."""

    __tablename__ = "clarification_questions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clarification_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ambiguous_event | new_location | workout_intensity | schedule_conflict | commute_time
    trigger: Mapped[str] = mapped_column(String(64), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    # yes_no | choice
    question_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="yes_no")
    # For choice: {"choices": ["Light", "Moderate", "Intense"]}
    options: Mapped[Optional[dict]] = mapped_column(JSONB)
    # The calendar event(s) that triggered this question
    context: Mapped[Optional[dict]] = mapped_column(JSONB)
    # Resolved answer (from user tap or assumption fallback)
    answer: Mapped[Optional[str]] = mapped_column(Text)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # What the morning job assumes if no answer received by expires_at
    assumption: Mapped[Optional[str]] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped[ClarificationSession] = relationship(back_populates="questions")


# ---------------------------------------------------------------------------
# Agent Memory & Observability
# ---------------------------------------------------------------------------


class AgentMemory(Base):
    """Long-lived facts the agent learns about the user over time."""

    __tablename__ = "agent_memories"
    __table_args__ = (Index("idx_agent_mem_user_kind", "user_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # preference | constraint | observation | habit
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=sa.text("0.80")
    )
    # user | inferred | clarification
    source: Mapped[Optional[str]] = mapped_column(Text)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # embedding column added in migration 002 when pgvector is enabled

    user: Mapped[User] = relationship(back_populates="agent_memories")


class MealHistoryIndex(Base):
    """Tracks recently suggested meals to prevent repetition."""

    __tablename__ = "meal_history_index"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Normalised meal title or recipe_id used as a deduplication key
    meal_signature: Mapped[str] = mapped_column(Text, primary_key=True)
    last_suggested: Mapped[date] = mapped_column(Date, nullable=False)
    times_30d: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("1"))


class AgentRun(Base):
    """Full observability record for every LLM step: cost, latency, tokens."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meal_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("meal_plans.id", ondelete="SET NULL")
    )
    # classify_calendar | plan_meals | finalize | send_email
    step: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(64))
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="agent_runs")


# ---------------------------------------------------------------------------
# Recipes & Ingredients — Phase 1: schema ready, LLM generates free text only
# ---------------------------------------------------------------------------


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    default_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    kcal_per_100: Mapped[Optional[float]] = mapped_column(Numeric(7, 2))
    protein_per_100: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    carbs_per_100: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))
    fat_per_100: Mapped[Optional[float]] = mapped_column(Numeric(6, 2))


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[Optional[str]] = mapped_column(Text)
    prep_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    cook_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    servings: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("1"))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=sa.text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )

    ingredients: Mapped[list[RecipeIngredient]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="RESTRICT"), primary_key=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    is_optional: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")


# ---------------------------------------------------------------------------
# Phase 2 Stubs — Household Inventory & Grocery Lists
# Foreign keys to ingredients already established above, so migrations are painless.
# ---------------------------------------------------------------------------


class Household(Base):
    __tablename__ = "households"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )

    members: Mapped[list[HouseholdMember]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
    inventory_items: Mapped[list[InventoryItem]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )
    grocery_lists: Mapped[list[GroceryList]] = relationship(
        back_populates="household", cascade="all, delete-orphan"
    )


class HouseholdMember(Base):
    __tablename__ = "household_members"

    household_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, server_default="member")

    household: Mapped[Household] = relationship(back_populates="members")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_on: Mapped[Optional[date]] = mapped_column(Date)
    # fridge | pantry | freezer
    location: Mapped[Optional[str]] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=sa.func.now(),
    )

    household: Mapped[Household] = relationship(back_populates="inventory_items")


class GroceryList(Base):
    __tablename__ = "grocery_lists"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[Optional[str]] = mapped_column(Text)
    # open | shopping | closed
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=sa.func.now()
    )

    household: Mapped[Household] = relationship(back_populates="grocery_lists")
    items: Mapped[list[GroceryListItem]] = relationship(
        back_populates="grocery_list", cascade="all, delete-orphan"
    )


class GroceryListItem(Base):
    __tablename__ = "grocery_list_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    grocery_list_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("grocery_lists.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ingredients.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    unit: Mapped[Optional[str]] = mapped_column(String(32))
    purchased: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())

    grocery_list: Mapped[GroceryList] = relationship(back_populates="items")
