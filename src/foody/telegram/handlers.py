"""
Callback query handler for Telegram inline keyboard replies.

Dispatches based on the callback_data prefix:
  q:{sequence}:{answer_code}   → clarification question answer
  fb:{YYYY-MM-DD}:{1-5}        → meal plan rating (feedback)

Called from api/telegram_webhook.py for every incoming Update.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import Bot, CallbackQuery, Update

from foody.agent.memory import consolidate_feedback
from foody.config import settings
from foody.db.engine import get_session
from foody.db.models import ClarificationSession, MealPlan, User
from foody.db.repositories.clarifications import record_answer
from foody.telegram.bot import update_digest_message
from foody.telegram.keyboards import resolve_answer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

async def handle_callback_query(update: Update) -> None:
    """Route incoming callback_query to the appropriate sub-handler."""
    query: CallbackQuery | None = update.callback_query
    if query is None or query.data is None:
        return

    data = query.data
    if data.startswith("q:"):
        await _handle_clarification_callback(query)
    elif data.startswith("fb:"):
        await _handle_feedback_callback(query)
    else:
        await query.answer("Unknown action.")


# ---------------------------------------------------------------------------
# Clarification answer handler
# ---------------------------------------------------------------------------

def _parse_clarification_data(data: str) -> tuple[int, str] | None:
    """Parse 'q:{seq}:{answer_code}' → (sequence, answer_code) or None."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "q":
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


async def _handle_clarification_callback(query: CallbackQuery) -> None:
    parsed = _parse_clarification_data(query.data)
    if parsed is None:
        await query.answer("Unknown action.")
        return

    sequence, answer_code = parsed
    telegram_user_id = str(query.from_user.id)

    async with get_session() as db:
        user_result = await db.execute(
            select(User).where(User.telegram_chat_id == telegram_user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            logger.warning("Callback from unknown Telegram user %s", telegram_user_id)
            await query.answer("I don't recognise this account. Please set up Foody first.")
            return

        session_result = await db.execute(
            select(ClarificationSession)
            .where(
                ClarificationSession.user_id == user.id,
                ClarificationSession.status.in_(["pending", "partially_answered"]),
            )
            .options(selectinload(ClarificationSession.questions))
            .order_by(ClarificationSession.plan_date.desc())
            .limit(1)
        )
        session = session_result.scalar_one_or_none()
        if session is None:
            await query.answer("This session has already been completed or expired.")
            return

        question = next(
            (q for q in session.questions if q.sequence == sequence), None
        )
        if question is None:
            await query.answer("Question not found.")
            return
        if question.answer is not None:
            await query.answer("Already answered!")
            return

        human_answer = resolve_answer(question, answer_code)
        await record_answer(db, session, sequence, human_answer)

        refreshed_result = await db.execute(
            select(ClarificationSession)
            .where(ClarificationSession.id == session.id)
            .options(selectinload(ClarificationSession.questions))
        )
        refreshed = refreshed_result.scalar_one()

    await query.answer(f"Got it — {human_answer}")

    if session.telegram_message_id:
        await update_digest_message(
            chat_id=telegram_user_id,
            message_id=session.telegram_message_id,
            plan_date=session.plan_date,
            questions=refreshed.questions,
        )


# ---------------------------------------------------------------------------
# Feedback rating handler
# ---------------------------------------------------------------------------

def _parse_feedback_data(data: str) -> tuple[date, int] | None:
    """Parse 'fb:{YYYY-MM-DD}:{1-5}' → (plan_date, rating) or None."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "fb":
        return None
    try:
        plan_date = date.fromisoformat(parts[1])
        rating = int(parts[2])
        if not 1 <= rating <= 5:
            return None
        return plan_date, rating
    except (ValueError, IndexError):
        return None


async def _handle_feedback_callback(query: CallbackQuery) -> None:
    parsed = _parse_feedback_data(query.data)
    if parsed is None:
        await query.answer("Unknown action.")
        return

    plan_date, rating = parsed
    telegram_user_id = str(query.from_user.id)

    user_id = None
    plan_id = None

    async with get_session() as db:
        user_result = await db.execute(
            select(User).where(User.telegram_chat_id == telegram_user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            await query.answer("Account not recognised.")
            return

        plan_result = await db.execute(
            select(MealPlan).where(
                MealPlan.user_id == user.id,
                MealPlan.plan_date == plan_date,
            )
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            await query.answer("Meal plan not found.")
            return
        if plan.overall_rating is not None:
            stars_done = "★" * plan.overall_rating + "☆" * (5 - plan.overall_rating)
            await query.answer(f"Already rated {stars_done}!")
            return

        plan.overall_rating = rating
        await db.commit()

        user_id = user.id
        plan_id = plan.id

    stars = "★" * rating + "☆" * (5 - rating)
    await query.answer(f"Thanks! {stars}")

    # Remove the rating keyboard from the message
    if query.message:
        try:
            async with Bot(token=settings.telegram_bot_token) as bot:
                await bot.edit_message_reply_markup(
                    chat_id=telegram_user_id,
                    message_id=query.message.message_id,
                    reply_markup=None,
                )
        except Exception:
            pass  # non-critical; old message or already edited

    # Run memory consolidation synchronously (Haiku is fast, ~1-3s)
    try:
        async with get_session() as db:
            plan_with_meals = await db.execute(
                select(MealPlan)
                .where(MealPlan.id == plan_id)
                .options(selectinload(MealPlan.meals))
            )
            plan_obj = plan_with_meals.scalar_one_or_none()
            if plan_obj:
                await consolidate_feedback(user_id=user_id, plan=plan_obj, rating=rating, db=db)
    except Exception:
        logger.exception("Feedback consolidation failed for user %s plan %s", user_id, plan_id)
