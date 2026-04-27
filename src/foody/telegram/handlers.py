"""
Callback query handler for Telegram inline keyboard replies.

Called from api/telegram_webhook.py for every incoming Update.

Flow:
  1. Parse callback_data  →  q:{sequence}:{answer_code}
  2. Identify user by telegram_chat_id
  3. Find their active ClarificationSession for the relevant plan_date
  4. Persist the answer via the clarifications repository
  5. Edit the digest message to reflect the new state
  6. Acknowledge the callback (removes the Telegram loading spinner)
"""

from __future__ import annotations

import logging

from telegram import Bot, CallbackQuery, Update
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from foody.db.engine import get_session
from foody.db.models import ClarificationSession, User
from foody.db.repositories.clarifications import get_session_by_id, record_answer
from foody.telegram.bot import update_digest_message
from foody.telegram.keyboards import resolve_answer

logger = logging.getLogger(__name__)


def _parse_callback_data(data: str) -> tuple[int, str] | None:
    """Parse 'q:{seq}:{answer_code}' → (sequence, answer_code) or None if malformed."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "q":
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


async def handle_callback_query(update: Update) -> None:
    query: CallbackQuery | None = update.callback_query
    if query is None or query.data is None:
        return

    parsed = _parse_callback_data(query.data)
    if parsed is None:
        await query.answer("Unknown action.")
        return

    sequence, answer_code = parsed
    telegram_user_id = str(query.from_user.id)

    async with get_session() as db:
        # Look up user by Telegram chat ID
        user_result = await db.execute(
            select(User).where(User.telegram_chat_id == telegram_user_id)
        )
        user = user_result.scalar_one_or_none()

        if user is None:
            logger.warning("Received callback from unknown Telegram user %s", telegram_user_id)
            await query.answer("I don't recognise this account. Please set up Foody first.")
            return

        # Find the most recent pending/partially-answered session for this user
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

        # Resolve and persist the answer
        human_answer = resolve_answer(question, answer_code)
        await record_answer(db, session, sequence, human_answer)

        # Reload questions after the update so build_digest_text sees fresh state
        refreshed_result = await db.execute(
            select(ClarificationSession)
            .where(ClarificationSession.id == session.id)
            .options(selectinload(ClarificationSession.questions))
        )
        refreshed = refreshed_result.scalar_one()

    # Acknowledge the button tap (removes loading spinner in Telegram)
    await query.answer(f"Got it — {human_answer}")

    # Edit the digest message to show the updated state
    if session.telegram_message_id:
        await update_digest_message(
            chat_id=telegram_user_id,
            message_id=session.telegram_message_id,
            plan_date=session.plan_date,
            questions=refreshed.questions,
        )
