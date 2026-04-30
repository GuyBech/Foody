"""
Callback query handler for Telegram inline keyboard replies.

Dispatches based on the callback_data prefix:
  q:{sequence}:{answer_code}   → clarification question answer
  fb:{YYYY-MM-DD}:{1-5}        → meal plan rating (feedback)

Called from api/telegram_webhook.py for every incoming Update.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from telegram import Bot, CallbackQuery, Message, Update

from foody.agent.memory import consolidate_feedback
from foody.config import settings
from foody.db.engine import get_session
from foody.db.models import ClarificationSession, MealPlan, User
from foody.db.repositories.clarifications import record_answer
from foody.jobs.plan_generation import generate_and_deliver_plan
from foody.telegram.bot import update_digest_message
from foody.telegram.keyboards import resolve_answer

logger = logging.getLogger(__name__)

_LOCAL_TZ = ZoneInfo("Asia/Jerusalem")

# Conversational states (User.current_state). NULL = idle.
_STATE_WAITING_FOR_CHANGES = "WAITING_FOR_CHANGES"


def _tomorrow_local() -> date:
    """Return tomorrow in Asia/Jerusalem regardless of the server's clock."""
    return (datetime.now(tz=_LOCAL_TZ) + timedelta(days=1)).date()


async def _get_user_by_telegram_chat(chat_id: str) -> User | None:
    async with get_session() as db:
        result = await db.execute(
            select(User).where(User.telegram_chat_id == chat_id)
        )
        return result.scalar_one_or_none()


async def _set_user_state(user_id, state: str | None) -> None:
    async with get_session() as db:
        user = await db.get(User, user_id)
        if user is None:
            return
        user.current_state = state
        await db.commit()


# ---------------------------------------------------------------------------
# Message handler — replies to plain user messages (text / commands).
# Foody is a scheduled-digest bot, not a chat bot, so we route everything to
# a small set of canned replies and let the cron jobs do the real work.
# ---------------------------------------------------------------------------

_WELCOME = (
    "👋 Hi! I'm <b>Foody</b>.\n\n"
    "I plan your meals around your calendar. Each evening (~20:00) I'll send "
    "a digest with a few quick questions about tomorrow's schedule, and each "
    "morning (~06:00) I'll send your meal plan.\n\n"
    "You interact with me by tapping the buttons in those messages — I don't "
    "take free-text commands yet.\n\n"
    "Use /help to see this message again."
)

_FALLBACK = (
    "I work via daily digests, not chat. You'll get an evening digest with "
    "buttons to answer, then a morning meal plan. Use /help for details."
)


async def handle_message(update: Update) -> None:
    """Handle plain incoming messages.

    Two modes:
      1. If the user is in state WAITING_FOR_CHANGES (set by tapping
         "✍️ יש שינויים אחרים"), the next text message is treated as a
         meal-planner override. We trigger plan generation, deliver, and
         clear the state. Slash-commands are exempt — they cancel the wait.
      2. Otherwise: canned welcome / fallback replies.
    """
    message: Message | None = update.message or update.edited_message
    if message is None or message.chat is None:
        return

    text = (message.text or "").strip()
    chat_id = message.chat.id

    if not text:
        logger.info("Ignoring non-text message from chat %s", chat_id)
        return

    user = await _get_user_by_telegram_chat(str(chat_id))
    is_command = text.startswith("/")

    # ── Mode 1: free-text intake while waiting for changes ──────────────────
    if user is not None and user.current_state == _STATE_WAITING_FOR_CHANGES and not is_command:
        await _set_user_state(user.id, None)  # clear before slow work
        try:
            async with Bot(token=settings.telegram_bot_token) as bot:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"קיבלתי. מתכנן עם השינויים: <i>{text}</i>",
                    parse_mode="HTML",
                )
        except Exception:
            logger.exception("Failed to send acknowledgement before planning")

        try:
            await generate_and_deliver_plan(
                user_id=user.id,
                plan_date=_tomorrow_local(),
                override_text=text,
            )
        except Exception:
            logger.exception("Plan generation from custom_changes failed")
            try:
                async with Bot(token=settings.telegram_bot_token) as bot:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="הייתה תקלה. נסה שוב או הקלד /start.",
                    )
            except Exception:
                logger.exception("Could not even send the error message")
        return

    # If a slash command arrives while waiting, treat it as cancelling the wait.
    if user is not None and user.current_state == _STATE_WAITING_FOR_CHANGES and is_command:
        await _set_user_state(user.id, None)

    # ── Mode 2: canned replies ───────────────────────────────────────────────
    if text.startswith("/start") or text.startswith("/help"):
        reply = _WELCOME
    else:
        reply = _FALLBACK

    async with Bot(token=settings.telegram_bot_token) as bot:
        await bot.send_message(chat_id=chat_id, text=reply, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Callback query dispatcher
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
    elif data in _EVENING_SUMMARY_ACTIONS:
        await _handle_evening_summary_callback(query)
    else:
        await query.answer("Unknown action.")


# ---------------------------------------------------------------------------
# Evening summary handler — interactive flow stub.
# Logs the action and acknowledges so Telegram's loading spinner stops.
# Real planning behaviour (kick off plan_meals on plan_all_ok, request free
# text on custom_changes, etc.) will be wired up in a follow-up.
# ---------------------------------------------------------------------------

_EVENING_SUMMARY_ACTIONS = {"plan_all_ok", "cancel_workout", "custom_changes"}

_CANCEL_WORKOUT_OVERRIDE = (
    "USER OVERRIDE: The evening workout/CrossFit is CANCELLED for this day. "
    "Do NOT include heavy pre-workout or post-workout nutrition for any "
    "evening workout. Treat the evening as a low-activity period."
)

_CUSTOM_CHANGES_PROMPT = "מה השתנה בלוז או במקרר? (פשוט הקלד את השינוי)"


async def _handle_evening_summary_callback(query: CallbackQuery) -> None:
    data = query.data
    telegram_chat_id = str(query.from_user.id) if query.from_user else None
    logger.info("Evening summary callback: data=%s telegram_user=%s", data, telegram_chat_id)

    if telegram_chat_id is None:
        await query.answer("התקבל")
        return

    user = await _get_user_by_telegram_chat(telegram_chat_id)
    if user is None:
        await query.answer("חשבון לא מזוהה.")
        return

    if data == "plan_all_ok":
        await query.answer("מתחיל לתכנן 🍽")
        try:
            await generate_and_deliver_plan(
                user_id=user.id,
                plan_date=_tomorrow_local(),
            )
        except Exception:
            logger.exception("plan_all_ok generation failed for user %s", user.id)

    elif data == "cancel_workout":
        await query.answer("ביטלתי את האימון 💪")
        try:
            await generate_and_deliver_plan(
                user_id=user.id,
                plan_date=_tomorrow_local(),
                override_text=_CANCEL_WORKOUT_OVERRIDE,
            )
        except Exception:
            logger.exception("cancel_workout generation failed for user %s", user.id)

    elif data == "custom_changes":
        await query.answer("כתבי לי בהודעה את השינויים ✍️")
        await _set_user_state(user.id, _STATE_WAITING_FOR_CHANGES)
        try:
            async with Bot(token=settings.telegram_bot_token) as bot:
                await bot.send_message(
                    chat_id=telegram_chat_id,
                    text=_CUSTOM_CHANGES_PROMPT,
                )
        except Exception:
            logger.exception("Failed to send custom_changes prompt")
            # Roll back the state so the user isn't stuck waiting for a
            # prompt they never got.
            await _set_user_state(user.id, None)

    else:
        await query.answer("התקבל")


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
                print(
                    f"DEBUG: Attempting to call Anthropic — consolidate_feedback "
                    f"user={user_id} plan={plan_id} rating={rating}",
                    flush=True,
                )
                await consolidate_feedback(user_id=user_id, plan=plan_obj, rating=rating, db=db)
                print("DEBUG: Anthropic consolidate_feedback returned", flush=True)
    except Exception as exc:
        print(f"DEBUG: Error encountered: {type(exc).__name__}: {exc}", flush=True)
        logger.exception("Feedback consolidation failed for user %s plan %s", user_id, plan_id)
