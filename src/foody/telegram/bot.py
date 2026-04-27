"""
Telegram Bot facade.

Responsibilities:
- Send the evening digest message (single message, inline keyboard, all questions)
- Edit the digest message in-place when a question is answered
- Send a standalone feedback rating request for today's meal plan
- Handle the "all done" terminal state

The Bot instance is created per-call (stateless) so it works in Vercel serverless.
"""

from __future__ import annotations

from datetime import date

from telegram import Bot

from foody.config import settings
from foody.db.models import ClarificationQuestion, ClarificationSession
from foody.telegram.keyboards import build_digest_keyboard, build_feedback_keyboard


def _make_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


def _format_date(d: date) -> str:
    return d.strftime("%A, %B %-d")


def _question_line(q: ClarificationQuestion, idx: int) -> str:
    number = f"{idx + 1}."
    if q.answer is not None:
        return f"✅ <b>{number}</b> {q.question_text}\n    → <i>{q.answer}</i>"
    return f"❓ <b>{number}</b> {q.question_text}"


def build_digest_text(
    questions: list[ClarificationQuestion],
    plan_date: date,
) -> str:
    lines = [
        f"🌙 <b>Foody Evening Digest – {_format_date(plan_date)}</b>\n",
        "I have a few quick questions about tomorrow's schedule before I plan your meals:\n",
    ]
    for i, q in enumerate(questions):
        lines.append(_question_line(q, i))

    unanswered = sum(1 for q in questions if q.answer is None)
    if unanswered == 0:
        lines.append("\n✅ <b>All set!</b> I'll have your meal plan ready by 06:00. 🍽")

    return "\n".join(lines)


async def send_evening_digest(
    chat_id: str,
    plan_date: date,
    questions: list[ClarificationQuestion],
) -> int:
    """Send the initial evening digest. Returns the Telegram message_id."""
    async with _make_bot() as bot:
        text = build_digest_text(questions, plan_date)
        keyboard = build_digest_keyboard(questions)
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    return msg.message_id


async def update_digest_message(
    chat_id: str,
    message_id: int,
    plan_date: date,
    questions: list[ClarificationQuestion],
) -> None:
    """Edit the existing digest message after a question is answered."""
    text = build_digest_text(questions, plan_date)
    keyboard = build_digest_keyboard(questions)
    async with _make_bot() as bot:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


async def send_feedback_request(
    chat_id: str,
    plan_date: date,
    plan_summary: str | None = None,
) -> int:
    """
    Send a standalone star-rating message for today's delivered meal plan.
    Returns the Telegram message_id (stored in meal_plans.feedback_telegram_msg_id).
    """
    lines = [
        f"⭐ <b>How was today's meal plan?</b>",
        f"<i>{_format_date(plan_date)}</i>",
    ]
    if plan_summary:
        lines.append(f"\n{plan_summary}")
    lines.append("\nTap a rating — this helps me improve your future plans.")

    text = "\n".join(lines)
    async with _make_bot() as bot:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=build_feedback_keyboard(plan_date),
            parse_mode="HTML",
        )
    return msg.message_id


async def send_error_notification(chat_id: str, message: str) -> None:
    """Notify the user of an agent error (used by cron jobs)."""
    async with _make_bot() as bot:
        await bot.send_message(chat_id=chat_id, text=f"⚠️ Foody error: {message}")
