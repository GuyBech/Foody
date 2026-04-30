"""
Morning Run Job — runs at 06:00 local time (03:00 UTC via Vercel cron).

The actual planning + persistence + delivery is in plan_generation.py and is
shared with the interactive evening flow. This file just resolves the
clarification session (legacy assumption fallbacks) and delegates.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from foody.db.engine import get_session
from foody.db.repositories.clarifications import apply_assumptions
from foody.jobs.plan_generation import generate_and_deliver_plan

logger = logging.getLogger(__name__)


async def _resolve_clarifications(
    user_id: uuid.UUID,
    plan_date: date,
) -> tuple[list[str], dict[str, str]]:
    """Apply assumption fallbacks to any unanswered clarification questions for
    plan_date. Returns (assumptions_log, answered_context).

    Legacy path — the new interactive evening flow does not create
    ClarificationSessions, so this is usually a no-op.
    """
    from foody.db.models import ClarificationSession

    assumptions_log: list[str] = []
    answered_context: dict[str, str] = {}

    async with get_session() as db:
        session_result = await db.execute(
            select(ClarificationSession)
            .where(
                ClarificationSession.user_id == user_id,
                ClarificationSession.plan_date == plan_date,
                ClarificationSession.status.in_(
                    ["pending", "partially_answered", "fully_answered"]
                ),
            )
            .options(selectinload(ClarificationSession.questions))
        )
        clarification_session = session_result.scalar_one_or_none()

        if clarification_session is None:
            return assumptions_log, answered_context

        unanswered = [q for q in clarification_session.questions if q.answer is None]
        if unanswered:
            logger.info(
                "%d unanswered clarification(s) for user %s — applying assumptions",
                len(unanswered), user_id,
            )
            assumptions_log = await apply_assumptions(db, clarification_session)
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

    return assumptions_log, answered_context


async def run_morning_run(user_id: uuid.UUID) -> None:
    """Scheduled-cron entrypoint: plan today's meals if not already done."""
    logger.info("Morning run started for user %s", user_id)
    today = date.today()

    assumptions_log, answered_context = await _resolve_clarifications(user_id, today)

    delivered = await generate_and_deliver_plan(
        user_id=user_id,
        plan_date=today,
        answered_context=answered_context,
        assumptions_log=assumptions_log,
    )

    if not delivered:
        logger.warning("Morning run for user %s did not deliver a plan", user_id)
