"""
Evening Prep Job — runs at ~20:00 local time (17:00 UTC via Vercel cron).

Pipeline:
  1. Fetch tomorrow's events from all configured Google Calendar IDs
  2. Upsert raw events into the DB
  3. Classify every event via LLM (→ category, intensity, is_at_home, commute,
     nutritional_flag, and ready-made clarification questions)
     └─ Falls back to rule-based heuristics if the LLM call fails
  4. Write classification results back to calendar_events rows + log AgentRun
  5. Build ClarificationQuestions from events where clarification_needed=True
  6. Create a ClarificationSession and send a single Telegram digest message
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from foody.agent.calendar_classifier import ClassificationResult, classify_calendar_events
from foody.agent.schemas import EventClassification
from foody.calendar.google_client import fetch_events_for_calendars, parse_event_times
from foody.config import settings
from foody.db.engine import get_session
from foody.db.models import AgentRun, CalendarEvent, User, UserIntegration
from foody.db.repositories.clarifications import create_session, set_telegram_message_id
from foody.telegram.bot import send_error_notification, send_evening_digest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule-based fallback classifier (used when LLM is unavailable)
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


@dataclass
class QuestionDraft:
    trigger: str
    question_text: str
    question_type: str = "yes_no"
    options: dict | None = None
    context: dict | None = None
    assumption: str | None = None


def _build_questions_rule_based(events: list[CalendarEvent]) -> list[QuestionDraft]:
    """Fallback question builder — fires only when LLM classifier fails."""
    questions: list[QuestionDraft] = []
    for event in events:
        if event.is_all_day:
            continue
        title = event.title or "Untitled"
        time_str = event.starts_at.strftime("%H:%M")
        duration_min = int((event.ends_at - event.starts_at).total_seconds() / 60)

        if event.event_category == "workout" and event.intensity is None:
            questions.append(
                QuestionDraft(
                    trigger="workout_intensity",
                    question_text=f'Your {time_str} session "{title}" – what\'s the intensity?',
                    question_type="choice",
                    options={"choices": ["Light", "Moderate", "Intense", "Skip it"]},
                    context={"event_id": str(event.id)},
                    assumption="Moderate",
                )
            )
        if event.event_category in ("work", "study") and event.location is None and event.is_at_home is None:
            questions.append(
                QuestionDraft(
                    trigger="location",
                    question_text=f'"{title}" at {time_str} – will you be at home or commuting?',
                    question_type="yes_no",
                    context={"event_id": str(event.id)},
                    assumption="No",
                )
            )
        if duration_min > 180 and event.event_category in ("work", "study"):
            questions.append(
                QuestionDraft(
                    trigger="schedule_conflict",
                    question_text=(
                        f'"{title}" runs {duration_min // 60}h {duration_min % 60}m from {time_str}. '
                        f"Can you step away for lunch?"
                    ),
                    question_type="yes_no",
                    context={"event_id": str(event.id)},
                    assumption="Yes",
                )
            )
    return questions


# ---------------------------------------------------------------------------
# LLM classification → QuestionDraft converter
# ---------------------------------------------------------------------------

def _questions_from_llm_classifications(
    classifications: list[EventClassification],
    event_map: dict[str, CalendarEvent],
) -> list[QuestionDraft]:
    questions: list[QuestionDraft] = []
    for cls in classifications:
        if not cls.clarification_needed:
            continue
        if not cls.suggested_question:
            continue
        event = event_map.get(cls.event_id)
        questions.append(
            QuestionDraft(
                trigger=cls.clarification_trigger or "unknown",
                question_text=cls.suggested_question,
                question_type=cls.suggested_question_type or "yes_no",
                options={"choices": cls.suggested_choices} if cls.suggested_choices else None,
                context={
                    "event_id": str(event.id) if event else None,
                    "event_title": event.title if event else None,
                    "llm_category": cls.category,
                    "llm_intensity": cls.intensity,
                },
                assumption=cls.suggested_assumption,
            )
        )
    return questions


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_user_with_integration(
    user_id: uuid.UUID,
) -> tuple[User, UserIntegration] | None:
    async with get_session() as db:
        user_result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            return None

        # Eager-load profile so it's available outside the session
        from sqlalchemy.orm import selectinload
        user_result2 = await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.profile))
        )
        user = user_result2.scalar_one()

        integration_result = await db.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "google_calendar",
            )
        )
        integration = integration_result.scalar_one_or_none()
        if integration is None:
            logger.warning("No Google Calendar integration for user %s", user_id)
            return None
        return user, integration


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
            category = _classify_title_rule_based(title)  # initial rough pass

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


async def _apply_classifications(
    events: list[CalendarEvent],
    classifications: list[EventClassification],
) -> None:
    """Write LLM classification fields back to calendar_events rows."""
    cls_map = {c.event_id: c for c in classifications}
    now = datetime.now(timezone.utc)

    async with get_session() as db:
        for event in events:
            cls = cls_map.get(event.external_id)
            if cls is None:
                continue
            await db.execute(
                update(CalendarEvent)
                .where(CalendarEvent.id == event.id)
                .values(
                    event_category=cls.category,
                    intensity=cls.intensity if cls.intensity != "none" else None,
                    is_at_home=cls.is_at_home,
                    needs_clarification=cls.clarification_needed,
                    classified_at=now,
                    classifier_version="llm-v1",
                )
            )
        await db.commit()


async def _log_agent_run(
    user_id: uuid.UUID,
    result: ClassificationResult,
    error: str | None = None,
) -> None:
    async with get_session() as db:
        db.add(
            AgentRun(
                user_id=user_id,
                step="classify_calendar",
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.estimated_cost_usd,
                latency_ms=result.latency_ms,
                error=error,
                finished_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Main job entrypoint
# ---------------------------------------------------------------------------

async def run_evening_prep(user_id: uuid.UUID) -> None:
    """Full evening prep pipeline for one user."""
    logger.info("Evening prep started for user %s", user_id)
    tomorrow = date.today() + timedelta(days=1)

    pair = await _get_user_with_integration(user_id)
    if pair is None:
        logger.error("Cannot run evening prep for user %s: missing user or integration", user_id)
        return

    user, integration = pair

    if not user.telegram_chat_id:
        logger.warning("User %s has no Telegram chat ID — cannot send digest", user_id)
        return

    # 1. Fetch events from all configured calendars
    try:
        raw_events = await fetch_events_for_calendars(
            access_token=integration.access_token or "",
            refresh_token=integration.refresh_token or "",
            target_date=tomorrow,
            calendar_ids=settings.calendar_id_list,
        )
    except Exception as exc:
        logger.exception("Google Calendar fetch failed for user %s", user_id)
        await send_error_notification(user.telegram_chat_id, f"Could not fetch your calendar: {exc}")
        return

    logger.info("Fetched %d event(s) from %d calendar(s)", len(raw_events), len(settings.calendar_id_list))

    # 2. Upsert into DB
    events = await _upsert_events(user_id, raw_events)
    non_all_day = [e for e in events if not e.is_all_day]

    if not events:
        logger.info("No events tomorrow for user %s — no digest needed", user_id)
        return

    # 3. LLM classification (with rule-based fallback)
    event_map = {e.external_id: e for e in non_all_day}
    questions: list[QuestionDraft]

    try:
        result = await classify_calendar_events(
            events=non_all_day,
            profile=user.profile,
            plan_date=tomorrow,
        )
        await _apply_classifications(non_all_day, result.classifications)
        await _log_agent_run(user_id, result)
        questions = _questions_from_llm_classifications(result.classifications, event_map)
        logger.info(
            "LLM classified %d event(s) → %d clarification(s) needed. "
            "Cost: $%.4f, latency: %dms",
            len(non_all_day),
            len(questions),
            result.estimated_cost_usd,
            result.latency_ms,
        )
    except Exception:
        logger.exception("LLM classifier failed — using rule-based fallback for user %s", user_id)
        questions = _build_questions_rule_based(non_all_day)

    if not questions:
        logger.info("No clarifications needed for user %s on %s", user_id, tomorrow)
        return

    # 4. Create ClarificationSession and send Telegram digest
    expires_at = datetime.combine(tomorrow, datetime.min.time()).replace(
        tzinfo=timezone.utc
    ) + timedelta(hours=5, minutes=45)  # expires at 05:45 UTC (before the 06:00 morning cron)

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

    logger.info(
        "Evening digest sent (message_id=%s, %d question(s)) for user %s",
        message_id, len(questions), user_id,
    )
