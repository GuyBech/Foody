"""
Reusable meal-plan generation + delivery pipeline.

Used by:
  - morning_run.py  (scheduled cron at 06:00 local for today's plan)
  - telegram/handlers.py  (interactive evening callbacks for tomorrow's plan)

The flow is:
  1. Load user + profile.
  2. Bail if a plan already exists for plan_date (idempotency).
  3. Create the MealPlan row in status="generating".
  4. Load context: events, leftovers, dietary profile, meal history.
  5. Call plan_meals() — LLM with self-correcting retry.
  6. Persist Meal rows + AgentRun (cost/latency).
  7. Update MealHistoryIndex (anti-repetition).
  8. Send via email (non-blocking) and Telegram (independent, both attempted).
  9. Mark MealPlan as sent / generated based on what got through.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone, time as dt_time

from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from foody.agent.meal_planner import MealPlanResult, plan_meals
from foody.agent.memory import load_dietary_profile, load_meal_history, update_meal_history
from foody.db.engine import get_session
from foody.db.models import AgentRun, CalendarEvent, Leftover, Meal, MealPlan, User
from foody.delivery.email import send_meal_plan_email
from foody.telegram.bot import send_error_notification, send_meal_plan_telegram

logger = logging.getLogger(__name__)

_PLAN_MODEL = "claude-haiku-4-5"


async def _plan_exists(user_id: uuid.UUID, plan_date: date) -> bool:
    async with get_session() as db:
        result = await db.execute(
            select(MealPlan.id).where(
                MealPlan.user_id == user_id,
                MealPlan.plan_date == plan_date,
            )
        )
        return result.scalar_one_or_none() is not None


async def generate_and_deliver_plan(
    user_id: uuid.UUID,
    plan_date: date,
    *,
    override_text: str | None = None,
    answered_context: dict[str, str] | None = None,
    assumptions_log: list[str] | None = None,
) -> bool:
    """Generate, persist, and deliver a meal plan for plan_date.

    Returns True if a plan was successfully delivered to at least one channel,
    False if anything blocked or failed. Designed to never raise — all errors
    are logged and signalled via Telegram error notifications when possible.
    """
    answered_context = answered_context or {}
    assumptions_log = assumptions_log or []

    logger.info(
        "Plan generation started: user=%s plan_date=%s override=%s",
        user_id, plan_date, "yes" if override_text else "no",
    )

    # ── Load user + profile ─────────────────────────────────────────────────
    async with get_session() as db:
        user_result = await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.profile))
        )
        user = user_result.scalar_one_or_none()

    if user is None:
        logger.error("User %s not found", user_id)
        return False

    profile = user.profile

    # ── Idempotency: skip if a plan already exists for this date ─────────────
    if await _plan_exists(user_id, plan_date):
        logger.info(
            "Plan for user=%s date=%s already exists — skipping regeneration",
            user_id, plan_date,
        )
        if user.telegram_chat_id:
            try:
                await send_error_notification(
                    user.telegram_chat_id,
                    f"כבר תכננתי לך אוכל ל־{plan_date.isoformat()}. שלח /reset כדי להתחיל מחדש.",
                )
            except Exception:
                logger.exception("Failed to notify user about existing plan")
        return False

    # ── Create MealPlan row ─────────────────────────────────────────────────
    async with get_session() as db:
        meal_plan = MealPlan(
            user_id=user_id,
            plan_date=plan_date,
            status="generating",
            assumptions_made="\n".join(assumptions_log) if assumptions_log else None,
        )
        db.add(meal_plan)
        await db.commit()
        await db.refresh(meal_plan)
        meal_plan_id = meal_plan.id

    # ── Load context (single session, closed before LLM call) ────────────────
    day_start = datetime.combine(plan_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    async with get_session() as db:
        events_result = await db.execute(
            select(CalendarEvent)
            .where(
                and_(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.starts_at >= day_start,
                    CalendarEvent.starts_at < day_end,
                )
            )
            .order_by(CalendarEvent.starts_at)
        )
        events: list[CalendarEvent] = list(events_result.scalars().all())

        leftovers_result = await db.execute(
            select(Leftover).where(
                Leftover.user_id == user_id,
                Leftover.is_active.is_(True),
            )
        )
        leftovers: list[Leftover] = list(leftovers_result.scalars().all())

        dietary_profile = await load_dietary_profile(user_id, db)
        meal_history = await load_meal_history(user_id, db, days=14)

    logger.info(
        "Context loaded: %d events, %d leftovers, %d history entries",
        len(events), len(leftovers), len(meal_history),
    )

    # ── LLM plan_meals (with self-correcting retry) ─────────────────────────
    result: MealPlanResult
    try:
        result = await plan_meals(
            user_id=user_id,
            profile=profile,
            events=events,
            answered_context=answered_context,
            assumptions_log=assumptions_log,
            plan_date=plan_date,
            dietary_profile=dietary_profile,
            meal_history=meal_history,
            leftovers=leftovers,
            override_text=override_text,
            model=_PLAN_MODEL,
        )
    except Exception:
        logger.exception("Meal plan generation failed for user %s", user_id)
        async with get_session() as db:
            plan = await db.get(MealPlan, meal_plan_id)
            if plan:
                plan.status = "failed"
                plan.generated_at = datetime.now(timezone.utc)
                await db.commit()
        if user.telegram_chat_id:
            try:
                await send_error_notification(
                    user.telegram_chat_id,
                    "Failed to generate your meal plan. Please try again later.",
                )
            except Exception:
                logger.exception("Failed to send error notification")
        return False

    plan_output = result.output

    # ── Persist Meal rows + AgentRun ────────────────────────────────────────
    async with get_session() as db:
        plan = await db.get(MealPlan, meal_plan_id)
        if plan is None:
            logger.error("MealPlan %s disappeared between create and persist", meal_plan_id)
            return False

        plan.status = "generated"
        plan.summary = plan_output.summary
        plan.total_kcal = plan_output.total_kcal
        plan.total_protein_g = plan_output.total_protein_g
        plan.total_carbs_g = plan_output.total_carbs_g
        plan.total_fat_g = plan_output.total_fat_g
        plan.model_version = result.model
        plan.generated_at = datetime.now(timezone.utc)

        for i, m in enumerate(plan_output.meals):
            time_obj: dt_time | None = None
            if m.suggested_time:
                try:
                    h, mi = m.suggested_time.split(":")
                    time_obj = dt_time(int(h), int(mi))
                except (ValueError, TypeError):
                    pass

            db.add(
                Meal(
                    meal_plan_id=meal_plan_id,
                    slot=m.slot,
                    suggested_time=time_obj,
                    title=m.title,
                    description=m.description,
                    kcal=m.kcal,
                    protein_g=m.protein_g,
                    carbs_g=m.carbs_g,
                    fat_g=m.fat_g,
                    rationale=m.rationale,
                    context_tags=m.context_tags or [],
                    sequence=i,
                )
            )

        db.add(
            AgentRun(
                user_id=user_id,
                meal_plan_id=meal_plan_id,
                step="plan_meals",
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.estimated_cost_usd,
                latency_ms=result.latency_ms,
                finished_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    logger.info(
        "Plan generated: %d meals, %d kcal, $%.4f LLM cost, %dms",
        len(plan_output.meals), plan_output.total_kcal,
        result.estimated_cost_usd, result.latency_ms,
    )

    # ── Update MealHistoryIndex (anti-repetition) ────────────────────────────
    async with get_session() as db:
        await update_meal_history(
            user_id=user_id,
            titles=[m.title for m in plan_output.meals],
            plan_date=plan_date,
            db=db,
        )

    # ── Deliver: email + Telegram, independent and non-blocking ──────────────
    email_id = ""
    if user.email:
        email_id = await send_meal_plan_email(
            to_email=user.email,
            user_name=user.full_name or user.email.split("@")[0],
            plan_date=plan_date,
            plan=plan_output,
            assumptions="\n".join(assumptions_log) if assumptions_log else None,
        )
    email_delivered = bool(email_id)

    telegram_delivered = False
    if user.telegram_chat_id:
        try:
            await send_meal_plan_telegram(
                chat_id=user.telegram_chat_id,
                plan=plan_output,
                plan_date=plan_date,
            )
            telegram_delivered = True
        except Exception:
            logger.exception("Telegram delivery failed for user %s", user_id)

    # ── Mark sent / generated ───────────────────────────────────────────────
    async with get_session() as db:
        plan = await db.get(MealPlan, meal_plan_id)
        if plan:
            if email_delivered or telegram_delivered:
                plan.status = "sent"
                plan.sent_at = datetime.now(timezone.utc)
            else:
                plan.status = "generated"
            await db.commit()

    if email_delivered and telegram_delivered:
        logger.info("Plan delivered via email + Telegram for user %s", user_id)
    elif email_delivered:
        logger.info("Plan delivered via email for user %s", user_id)
    elif telegram_delivered:
        logger.info("Plan delivered via Telegram for user %s", user_id)
    else:
        logger.warning("Plan generated but NOT delivered for user %s", user_id)

    return email_delivered or telegram_delivered
