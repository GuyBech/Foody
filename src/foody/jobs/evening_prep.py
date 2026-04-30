"""
Evening Prep Job — runs at ~20:00 local time (17:00 UTC via Vercel cron).

New interactive flow (no automatic meal planning):
  0. Send today's plan-rating request if it hasn't been rated.
  1. Fetch tomorrow's events from all configured Google Calendars.
  2. Upsert raw events into the DB (so morning_run / future steps can read them).
  3. Fetch the user's active leftovers / batch-cooking items.
  4. Format calendar + leftovers into the "Transparent Calendar" text
     (calendar times rendered in Asia/Jerusalem).
  5. Send the summary via Telegram with action buttons (plan_all_ok /
     cancel_workout / custom_changes). plan_meals is NOT called here —
     planning is now triggered explicitly by user action.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from foody.calendar.google_client import fetch_events_for_calendars, parse_event_times
from foody.config import settings
from foody.db.engine import get_session
from foody.db.models import CalendarEvent, Leftover, MealPlan, User
from foody.telegram.bot import (
    send_error_notification,
    send_evening_summary,
    send_feedback_request,
)

logger = logging.getLogger(__name__)

_LOCAL_TZ = ZoneInfo("Asia/Jerusalem")


# ---------------------------------------------------------------------------
# Lightweight rule-based event_category fallback (used by _upsert_events to
# populate calendar_events.event_category — meal_planner reads it later).
# ---------------------------------------------------------------------------

_WORKOUT_KEYWORDS = {
    "gym", "workout", "training", "crossfit", "weightlifting", "functional",
    "run", "running", "swim", "swimming", "cycle", "cycling", "yoga", "pilates",
    "hiit", "cardio", "strength", "lift", "hyrox", "הירוקס", "קרוספיט",
    "וואד", "wod", "הרמת משקולות", "סנאץ", "קלין", "ריצה", "שחייה",
}
_STUDY_KEYWORDS = {
    "lecture", "class", "study", "university", "course", "exam", "tutorial",
    "הרצאה", "מעבדה", "בחינה", "מבחן", "תרגול", "סמינר", "פרויקט", "tau", "אוניברסיטה",
}
_WORK_KEYWORDS = {"meeting", "standup", "sync", "call", "client", "office", "interview"}
_MILITARY_KEYWORDS = {"מילואים", "מלואים", "מיל'", "reserve"}


def _classify_title_rule_based(title: str) -> str:
    lower = title.lower()
    if any(kw in lower for kw in _MILITARY_KEYWORDS):
        return "military"
    if any(kw in lower for kw in _WORKOUT_KEYWORDS):
        return "workout"
    if any(kw in lower for kw in _STUDY_KEYWORDS):
        return "study"
    if any(kw in lower for kw in _WORK_KEYWORDS):
        return "work"
    return "unknown"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_user(user_id: uuid.UUID) -> User | None:
    """Fetch the user with profile eager-loaded."""
    async with get_session() as db:
        result = await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.profile))
        )
        return result.scalar_one_or_none()


async def _upsert_events(
    user_id: uuid.UUID,
    raw_events: list[dict[str, Any]],
) -> list[CalendarEvent]:
    """Insert or update calendar events; return upserted ORM objects."""
    upserted: list[CalendarEvent] = []
    async with get_session() as db:
        for raw in raw_events:
            external_id = raw.get("_external_id") or raw.get("id", "")
            title = raw.get("summary", "")
            starts_at, ends_at, is_all_day = parse_event_times(raw)
            category = _classify_title_rule_based(title)

            stmt = (
                pg_insert(CalendarEvent)
                .values(
                    user_id=user_id,
                    external_id=external_id,
                    provider="google_calendar",
                    title=title,
                    description=raw.get("description"),
                    location=raw.get("location"),
                    starts_at=starts_at,
                    ends_at=ends_at,
                    is_all_day=is_all_day,
                    event_category=category,
                    raw_payload=raw,
                )
                .on_conflict_do_update(
                    index_elements=["user_id", "provider", "external_id"],
                    set_={
                        "title": title,
                        "description": raw.get("description"),
                        "location": raw.get("location"),
                        "starts_at": starts_at,
                        "ends_at": ends_at,
                        "event_category": category,
                        "raw_payload": raw,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                .returning(CalendarEvent)
            )
            result = await db.execute(stmt)
            upserted.append(result.scalar_one())
        await db.commit()
    return upserted


async def _load_active_leftovers(user_id: uuid.UUID) -> list[Leftover]:
    async with get_session() as db:
        result = await db.execute(
            select(Leftover)
            .where(Leftover.user_id == user_id, Leftover.is_active.is_(True))
            .order_by(Leftover.created_at)
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Text formatting — the "Transparent Calendar"
# ---------------------------------------------------------------------------

def _format_date_header(d: date) -> str:
    # %A is the weekday name; locale-dependent. Strftime gives English by default.
    return f"{d.strftime('%A, %B')} {d.day}"


def _format_calendar_text(events: list[CalendarEvent], plan_date: date) -> str:
    header = f"📅 <b>היומן שלך מחר — {_format_date_header(plan_date)}:</b>"
    if not events:
        return header + "\n<i>אין אירועים ביומן.</i>"

    lines = [header]
    sorted_events = sorted(events, key=lambda e: (not e.is_all_day, e.starts_at))
    for e in sorted_events:
        title = e.title or "(ללא שם)"
        if e.is_all_day:
            lines.append(f"• <b>(יום שלם)</b> {title}")
        else:
            start = e.starts_at.astimezone(_LOCAL_TZ).strftime("%H:%M")
            end = e.ends_at.astimezone(_LOCAL_TZ).strftime("%H:%M")
            lines.append(f"• <b>{start}–{end}</b> {title}")
    return "\n".join(lines)


def _format_leftovers_text(leftovers: list[Leftover]) -> str:
    header = "🥡 <b>שאריות פעילות:</b>"
    if not leftovers:
        return header + "\n<i>אין כרגע.</i>"
    lines = [header] + [f"• {lo.item_description}" for lo in leftovers]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Yesterday's-plan feedback request
# ---------------------------------------------------------------------------

async def _send_today_feedback_if_needed(user_id: uuid.UUID, chat_id: str) -> None:
    """If today's meal plan was delivered and hasn't been rated, send a star
    rating request before the evening summary."""
    today = date.today()
    async with get_session() as db:
        result = await db.execute(
            select(MealPlan).where(
                MealPlan.user_id == user_id,
                MealPlan.plan_date == today,
                MealPlan.status == "sent",
                MealPlan.overall_rating.is_(None),
                MealPlan.feedback_telegram_msg_id.is_(None),
            )
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            return

        try:
            msg_id = await send_feedback_request(
                chat_id=chat_id,
                plan_date=today,
                plan_summary=plan.summary,
            )
            plan.feedback_telegram_msg_id = msg_id
            await db.commit()
            logger.info("Feedback request sent for today's plan (user %s)", user_id)
        except Exception:
            logger.exception("Failed to send feedback request for user %s", user_id)


# ---------------------------------------------------------------------------
# Main job entrypoint
# ---------------------------------------------------------------------------

async def run_evening_prep(user_id: uuid.UUID) -> None:
    """Run the evening "Transparent Calendar" summary for one user."""
    logger.info("Evening prep started for user %s", user_id)
    tomorrow = date.today() + timedelta(days=1)

    user = await _get_user(user_id)
    if user is None:
        logger.error("Cannot run evening prep for user %s: user not found", user_id)
        return

    if not user.telegram_chat_id:
        logger.warning("User %s has no Telegram chat ID — skipping summary", user_id)
        return

    # 0. Yesterday's-plan feedback prompt (independent of the new summary).
    await _send_today_feedback_if_needed(user_id, user.telegram_chat_id)

    # 1. Fetch tomorrow's events from Google Calendar.
    try:
        raw_events = await fetch_events_for_calendars(
            target_date=tomorrow,
            calendar_ids=settings.calendar_id_list,
        )
    except Exception as exc:
        logger.exception("Google Calendar fetch failed for user %s", user_id)
        await send_error_notification(
            user.telegram_chat_id, f"Could not fetch your calendar: {exc}"
        )
        return

    logger.info(
        "Fetched %d event(s) from %d calendar(s)",
        len(raw_events), len(settings.calendar_id_list),
    )

    # 2. Persist events so morning_run / future LLM calls can read them.
    events = await _upsert_events(user_id, raw_events)

    # 3. Load active leftovers.
    leftovers = await _load_active_leftovers(user_id)

    # 4. Format the two text blocks.
    calendar_text = _format_calendar_text(events, tomorrow)
    leftovers_text = _format_leftovers_text(leftovers)

    # 5. Send the interactive summary (action buttons attached in bot.py).
    try:
        message_id = await send_evening_summary(
            chat_id=user.telegram_chat_id,
            calendar_text=calendar_text,
            leftovers_text=leftovers_text,
        )
    except Exception:
        logger.exception("Failed to send evening summary for user %s", user_id)
        return

    logger.info(
        "Evening summary sent (message_id=%s, %d event(s), %d leftover(s)) for user %s",
        message_id, len(events), len(leftovers), user_id,
    )


def _main() -> None:
    """CLI entrypoint: `python -m foody.jobs.evening_prep`."""
    import asyncio
    import os
    import sys

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw = os.getenv("FOODY_USER_ID") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not raw:
        raise SystemExit(
            "FOODY_USER_ID not set. Set it in .env or pass as the first argument."
        )

    asyncio.run(run_evening_prep(uuid.UUID(raw)))


if __name__ == "__main__":
    _main()
