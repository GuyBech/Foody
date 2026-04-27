"""
Evening Prep Job — runs at ~20:00 local time (17:00 UTC via Vercel cron).

Steps:
  1. Fetch Google Calendar events for tomorrow
  2. Upsert raw events into the DB
  3. Classify events with rule-based heuristics (LLM classification added in Step 3)
  4. Build a list of clarification questions
  5. If questions exist: create a ClarificationSession and send the Telegram digest
  6. If no questions: mark the day as ready for the morning job (no session needed)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from foody.calendar.google_client import fetch_events_for_date, parse_event_times
from foody.config import settings
from foody.db.engine import get_session
from foody.db.models import CalendarEvent, User, UserIntegration
from foody.db.repositories.clarifications import create_session, set_telegram_message_id
from foody.telegram.bot import send_evening_digest, send_error_notification

logger = logging.getLogger(__name__)

# Keywords used for rule-based event classification
_WORKOUT_KEYWORDS = {
    "gym", "workout", "training", "crossfit", "weightlifting", "functional",
    "run", "running", "swim", "swimming", "cycle", "cycling", "yoga", "pilates",
    "hiit", "cardio", "strength", "lift",
}
_STUDY_KEYWORDS = {"lecture", "class", "study", "university", "course", "exam", "tutorial"}
_WORK_KEYWORDS = {"meeting", "standup", "sync", "call", "client", "office", "interview"}
_COMMUTE_KEYWORDS = {"commute", "travel", "bus", "train", "drive", "taxi"}


@dataclass
class QuestionDraft:
    trigger: str
    question_text: str
    question_type: str = "yes_no"
    options: dict | None = None
    context: dict | None = None
    assumption: str | None = None


def _classify_title(title: str) -> str:
    """Return an event_category based on title keywords."""
    lower = title.lower()
    if any(kw in lower for kw in _WORKOUT_KEYWORDS):
        return "workout"
    if any(kw in lower for kw in _STUDY_KEYWORDS):
        return "study"
    if any(kw in lower for kw in _WORK_KEYWORDS):
        return "work"
    if any(kw in lower for kw in _COMMUTE_KEYWORDS):
        return "commute"
    return "unknown"


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _build_questions(events: list[CalendarEvent]) -> list[QuestionDraft]:
    """
    Rule-based heuristics to spot ambiguities that would degrade meal planning quality.
    LLM-based classification will replace/augment this in Step 3.
    """
    questions: list[QuestionDraft] = []

    for event in events:
        if event.is_all_day:
            continue

        title = event.title or "Untitled event"
        time_str = _fmt_time(event.starts_at)
        duration_min = int((event.ends_at - event.starts_at).total_seconds() / 60)

        # Workout with unknown intensity → affects pre/post-workout macro timing
        if event.event_category == "workout" and event.intensity is None:
            questions.append(
                QuestionDraft(
                    trigger="workout_intensity",
                    question_text=(
                        f'Your {time_str} session "{title}" – what\'s the intensity level?'
                    ),
                    question_type="choice",
                    options={"choices": ["Light", "Moderate", "Intense", "Skip it"]},
                    context={"event_id": str(event.id), "title": title},
                    assumption="Moderate",
                )
            )

        # Event without location and not obviously at home → affects meal convenience
        if event.event_category in ("work", "study") and event.location is None and event.is_at_home is None:
            questions.append(
                QuestionDraft(
                    trigger="event_location",
                    question_text=(
                        f'"{title}" at {time_str} – will you be at home or commuting?'
                    ),
                    question_type="yes_no",
                    context={"event_id": str(event.id), "title": title},
                    assumption="No",
                )
            )

        # Very long event (>3h) with no break → might crowd out a meal slot
        if duration_min > 180 and event.event_category in ("work", "study"):
            questions.append(
                QuestionDraft(
                    trigger="schedule_conflict",
                    question_text=(
                        f'"{title}" runs for {duration_min // 60}h {duration_min % 60}m '
                        f"starting {time_str}. Can you step away for lunch?"
                    ),
                    question_type="yes_no",
                    context={"event_id": str(event.id), "duration_min": duration_min},
                    assumption="Yes",
                )
            )

    return questions


async def _get_user_with_integration(
    user_id: uuid.UUID,
) -> tuple[User, UserIntegration] | None:
    async with get_session() as db:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            return None

        integration_result = await db.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "google_calendar",
            )
        )
        integration = integration_result.scalar_one_or_none()
        if integration is None:
            logger.warning("User %s has no Google Calendar integration", user_id)
            return None

        return user, integration


async def _upsert_events(
    user_id: uuid.UUID,
    raw_events: list[dict[str, Any]],
) -> list[CalendarEvent]:
    """Insert or update calendar events and return the ORM objects."""
    upserted: list[CalendarEvent] = []

    async with get_session() as db:
        for raw in raw_events:
            external_id = raw.get("id", "")
            title = raw.get("summary", "")
            starts_at, ends_at, is_all_day = parse_event_times(raw)

            category = _classify_title(title)

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
            event = result.scalar_one()
            upserted.append(event)

        await db.commit()

    return upserted


async def run_evening_prep(user_id: uuid.UUID) -> None:
    """Full evening prep pipeline for one user."""
    logger.info("Evening prep started for user %s", user_id)
    tomorrow = date.today() + timedelta(days=1)

    pair = await _get_user_with_integration(user_id)
    if pair is None:
        logger.error("Cannot run evening prep: user or integration missing for %s", user_id)
        return

    user, integration = pair

    if not user.telegram_chat_id:
        logger.warning("User %s has no Telegram chat ID; skipping", user_id)
        return

    try:
        raw_events = await fetch_events_for_date(
            access_token=integration.access_token or "",
            refresh_token=integration.refresh_token or "",
            target_date=tomorrow,
        )
    except Exception as exc:
        logger.exception("Google Calendar fetch failed for user %s", user_id)
        await send_error_notification(
            user.telegram_chat_id, f"Could not fetch your calendar: {exc}"
        )
        return

    events = await _upsert_events(user_id, raw_events)
    non_all_day = [e for e in events if not e.is_all_day]

    questions = _build_questions(non_all_day)

    if not questions:
        logger.info("No clarifications needed for user %s on %s", user_id, tomorrow)
        return

    logger.info(
        "Creating clarification session with %d question(s) for user %s",
        len(questions),
        user_id,
    )

    # Session expires at 05:45 next day (15 min before morning job runs)
    expires_at = datetime.combine(tomorrow, datetime.min.time()).replace(
        tzinfo=timezone.utc
    ) + timedelta(hours=5, minutes=45)

    async with get_session() as db:
        session = await create_session(
            db=db,
            user_id=user_id,
            plan_date=tomorrow,
            questions=[
                {
                    "trigger": q.trigger,
                    "question_text": q.question_text,
                    "question_type": q.question_type,
                    "options": q.options,
                    "context": q.context,
                    "assumption": q.assumption,
                }
                for q in questions
            ],
            expires_at=expires_at,
        )

        message_id = await send_evening_digest(
            chat_id=user.telegram_chat_id,
            plan_date=tomorrow,
            questions=session.questions,
        )

        await set_telegram_message_id(db, session, message_id)

    logger.info("Evening digest sent (message_id=%s) for user %s", message_id, user_id)
