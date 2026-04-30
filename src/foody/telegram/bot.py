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

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from foody.agent.schemas import SLOT_LABELS, MealPlanOutput
from foody.config import settings
from foody.db.models import ClarificationQuestion, ClarificationSession
from foody.telegram.keyboards import build_digest_keyboard, build_feedback_keyboard


def _make_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


def _format_date(d: date) -> str:
    # Avoid %-d (POSIX-only) / %#d (Windows-only) by formatting the day separately.
    return f"{d.strftime('%A, %B')} {d.day}"


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


def _format_meal_plan_text(plan: MealPlanOutput, plan_date: date) -> str:
    """Build the HTML body for the Telegram meal-plan summary."""
    lines = [f"🍽 <b>Foody Meal Plan – {_format_date(plan_date)}</b>"]
    if plan.summary:
        lines.append(f"<i>{plan.summary}</i>")
    lines.append("")
    for m in plan.meals:
        time_str = f"{m.suggested_time} · " if m.suggested_time else ""
        slot_label = SLOT_LABELS.get(m.slot, m.slot.replace("_", " ").title())
        lines.append(f"<b>{time_str}{slot_label}</b>")
        lines.append(f"  {m.title} — {m.kcal} kcal")
        if m.description:
            lines.append(f"  <i>{m.description}</i>")
    lines.append("")
    lines.append(
        f"<b>Daily total:</b> {plan.total_kcal} kcal · "
        f"P {plan.total_protein_g}g · C {plan.total_carbs_g}g · F {plan.total_fat_g}g"
    )
    return "\n".join(lines)


# Keywords that indicate a workout is scheduled. Match is substring,
# case-insensitive. "אימון" is Hebrew for "workout"/"training" and Hebrew
# has no case, so .lower() leaves it intact.
_WORKOUT_HINT_KEYWORDS = ("crossfit", "אימון", "workout", "run")

_BTN_PLAN_ALL_OK = InlineKeyboardButton("✅ בול, תכנן לי אוכל", callback_data="plan_all_ok")
_BTN_CANCEL_WORKOUT = InlineKeyboardButton("❌ בטל אימון ערב", callback_data="cancel_workout")
_BTN_CUSTOM_CHANGES = InlineKeyboardButton("✍️ יש שינויים אחרים", callback_data="custom_changes")


def _has_workout_hint(calendar_text: str) -> bool:
    haystack = calendar_text.lower()
    return any(kw in haystack for kw in _WORKOUT_HINT_KEYWORDS)


def _build_evening_summary_keyboard(calendar_text: str) -> InlineKeyboardMarkup:
    """Show the cancel-workout button only when the calendar mentions one."""
    rows = [[_BTN_PLAN_ALL_OK]]
    if _has_workout_hint(calendar_text):
        rows.append([_BTN_CANCEL_WORKOUT])
    rows.append([_BTN_CUSTOM_CHANGES])
    return InlineKeyboardMarkup(rows)


async def send_evening_summary(
    chat_id: str,
    calendar_text: str,
    leftovers_text: str,
) -> int:
    """Send the interactive evening "Transparent Calendar" summary.

    Combines the pre-formatted calendar and leftovers blocks into a single
    HTML message and attaches the action keyboard. The "❌ בטל אימון ערב"
    row is only shown when calendar_text mentions a workout-related keyword,
    so the button doesn't appear on non-workout days. Returns the Telegram
    message_id so the caller can edit/track it later.
    """
    parts = [p for p in (calendar_text, leftovers_text) if p]
    body = "\n\n".join(parts) if parts else "(אין נתונים להצגה)"

    if len(body) > 4000:
        body = body[:3990] + "\n…(truncated)"

    keyboard = _build_evening_summary_keyboard(calendar_text)
    async with _make_bot() as bot:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=body,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    return msg.message_id


async def send_meal_plan_telegram(
    chat_id: str,
    plan: MealPlanOutput,
    plan_date: date,
) -> int:
    """Send the day's meal plan to Telegram (used as fallback when email fails).

    Returns the Telegram message_id. May raise if Telegram itself is down —
    that's still better than silently dropping the plan.
    """
    text = _format_meal_plan_text(plan, plan_date)
    # Telegram caps text messages at 4096 chars — truncate just in case.
    if len(text) > 4000:
        text = text[:3990] + "\n…(truncated)"
    async with _make_bot() as bot:
        msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    return msg.message_id
