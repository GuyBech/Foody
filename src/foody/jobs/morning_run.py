"""
Morning Run Job — runs at 06:00 local time (03:00 UTC via Vercel cron).

Pipeline:
  1. Load user + profile
  2. Resolve ClarificationSession: apply assumption fallbacks to any unanswered questions
  3. Load today's CalendarEvents from DB (populated by last night's evening prep)
  4. Load AgentMemory dietary profile + MealHistoryIndex (anti-repetition list)
  5. Call plan_meals() — LLM generates a complete meal plan
  6. Persist MealPlan + individual Meal rows to DB
  7. Log AgentRun (cost, latency, tokens)
  8. Update MealHistoryIndex with today's meal titles
  9. Send HTML email via Resend
 10. Mark MealPlan status=sent
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone, time as dt_time

from sqlalchemy import and_, select
from sqlalchemy.orm import selectinload

from foody.agent.meal_planner import MealPlanResult, plan_meals
from foody.agent.memory import load_dietary_profile, load_meal_history, update_meal_history
from foody.config import settings
from foody.db.engine import get_session
from foody.db.models import AgentRun, CalendarEvent, Meal, MealPlan, User
from foody.db.repositories.clarifications import apply_assumptions
from foody.delivery.email import send_meal_plan_email
from foody.telegram.bot import send_error_notification, send_meal_plan_telegram

logger = logging.getLogger(__name__)

_PLAN_MODEL = "claude-haiku-4-5"


async def run_morning_run(user_id: uuid.UUID) -> None:
    """Full morning generation pipeline for one user."""
    logger.info("Morning run started for user %s", user_id)
    today = date.today()

    # ── Step 1: Load user + profile ──────────────────────────────────────────
    async with get_session() as db:
        result = await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.profile))
        )
        user = result.scalar_one_or_none()

    if user is None:
        logger.error("User %s not found", user_id)
        return
    if not user.email:
        logger.error("User %s has no email address — cannot deliver plan", user_id)
        return

    profile = user.profile  # may be None — plan_meals handles it gracefully

    # ── Step 2: Resolve clarification session ─────────────────────────────────
    assumptions_log: list[str] = []
    answered_context: dict[str, str] = {}

    async with get_session() as db:
        from foody.db.models import ClarificationSession
        session_result = await db.execute(
            select(ClarificationSession)
            .where(
                ClarificationSession.user_id == user_id,
                ClarificationSession.plan_date == today,
                ClarificationSession.status.in_(
                    ["pending", "partially_answered", "fully_answered"]
                ),
            )
            .options(selectinload(ClarificationSession.questions))
        )
        clarification_session = session_result.scalar_one_or_none()

        if clarification_session:
            unanswered = [q for q in clarification_session.questions if q.answer is None]
            if unanswered:
                logger.info(
                    "%d unanswered question(s) for user %s — applying assumptions",
                    len(unanswered), user_id,
                )
                assumptions_log = await apply_assumptions(db, clarification_session)
                # Reload to get the freshly populated answer fields
                await db.refresh(clarification_session)
                for q in clarification_session.questions:
                    await db.refresh(q)
            else:
                clarification_session.status = "resolved"
                await db.commit()

            answered_context = {
                q.trigger: q.answer
                for q in clarification_session.questions
                if q.answer is not None
            }

    # ── Step 3: Create MealPlan row ────────────────────────────────────────────
    meal_plan_id: uuid.UUID
    async with get_session() as db:
        meal_plan = MealPlan(
            user_id=user_id,
            plan_date=today,
            status="generating",
            assumptions_made="\n".join(assumptions_log) if assumptions_log else None,
        )
        db.add(meal_plan)
        await db.commit()
        await db.refresh(meal_plan)
        meal_plan_id = meal_plan.id

    # ── Step 4: Load context from DB ──────────────────────────────────────────
    # Load everything in one session, then close it before the LLM call so we
    # don't hold a DB connection open during the 10–30 second round-trip.
    today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    async with get_session() as db:
        events_result = await db.execute(
            select(CalendarEvent)
            .where(
                and_(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.starts_at >= today_start,
                    CalendarEvent.starts_at < today_end,
                )
            )
            .order_by(CalendarEvent.starts_at)
        )
        events: list[CalendarEvent] = list(events_result.scalars().all())

        dietary_profile = await load_dietary_profile(user_id, db)
        meal_history = await load_meal_history(user_id, db, days=14)

    logger.info(
        "Context loaded for user %s: %d events, %d history entries",
        user_id, len(events), len(meal_history),
    )

    # ── Step 5: LLM meal plan generation ─────────────────────────────────────
    result: MealPlanResult
    try:
        result = await plan_meals(
            user_id=user_id,
            profile=profile,
            events=events,
            answered_context=answered_context,
            assumptions_log=assumptions_log,
            plan_date=today,
            dietary_profile=dietary_profile,
            meal_history=meal_history,
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
            await send_error_notification(
                user.telegram_chat_id,
                "Failed to generate your meal plan. I'll retry shortly.",
            )
        return

    plan_output = result.output

    # ── Steps 6 + 7: Persist meals + AgentRun ────────────────────────────────
    async with get_session() as db:
        plan = await db.get(MealPlan, meal_plan_id)
        if plan is None:
            logger.error("MealPlan %s disappeared — aborting", meal_plan_id)
            return

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
        "Plan generated for user %s: %d meals, %d kcal, $%.4f LLM cost, %dms",
        user_id, len(plan_output.meals), plan_output.total_kcal,
        result.estimated_cost_usd, result.latency_ms,
    )

    # ── Step 8: Update MealHistoryIndex ───────────────────────────────────────
    async with get_session() as db:
        await update_meal_history(
            user_id=user_id,
            titles=[m.title for m in plan_output.meals],
            plan_date=today,
            db=db,
        )

    # ── Step 9: Send email (non-blocking — never raises) ─────────────────────
    email_id = await send_meal_plan_email(
        to_email=user.email,
        user_name=user.full_name or user.email.split("@")[0],
        plan_date=today,
        plan=plan_output,
        assumptions="\n".join(assumptions_log) if assumptions_log else None,
    )
    email_delivered = bool(email_id)

    # ── Step 9b: Telegram fallback if email failed ────────────────────────────
    # Goal: user receives the plan even when Resend is in sandbox / down.
    telegram_delivered = False
    if not email_delivered and user.telegram_chat_id:
        try:
            await send_meal_plan_telegram(
                chat_id=user.telegram_chat_id,
                plan=plan_output,
                plan_date=today,
            )
            telegram_delivered = True
            logger.info(
                "Plan delivered via Telegram fallback for user %s (email failed)",
                user_id,
            )
        except Exception:
            logger.exception(
                "Both email AND Telegram fallback failed for user %s", user_id,
            )

    # ── Step 10: Mark as sent / generated based on what got through ──────────
    async with get_session() as db:
        plan = await db.get(MealPlan, meal_plan_id)
        if plan:
            if email_delivered or telegram_delivered:
                plan.status = "sent"
                plan.sent_at = datetime.now(timezone.utc)
            else:
                plan.status = "generated"  # nothing reached the user
            await db.commit()

    if email_delivered:
        logger.info("Morning run complete for user %s — plan emailed to %s",
                    user_id, user.email)
    elif telegram_delivered:
        logger.info("Morning run complete for user %s — plan sent via Telegram",
                    user_id)
    else:
        logger.warning(
            "Morning run finished for user %s but plan was NOT delivered "
            "via email or Telegram", user_id,
        )
