"""
Morning Run Job — runs at 06:00 local time (03:00 UTC via Vercel cron).

Steps:
  1. Fetch today's ClarificationSession (if any) and apply assumption fallbacks
  2. Gather calendar context for today
  3. Recall agent memories
  4. Generate meal plan via LLM (stub — full implementation in Step 3)
  5. Send meal plan email via Resend

The fallback note ("I assumed X for Y") is included in the email when the user
did not reply to the Telegram digest.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from foody.db.engine import get_session
from foody.db.models import ClarificationSession, MealPlan, User
from foody.db.repositories.clarifications import apply_assumptions

logger = logging.getLogger(__name__)


async def run_morning_run(user_id: uuid.UUID) -> None:
    """Full morning generation pipeline for one user."""
    logger.info("Morning run started for user %s", user_id)
    today = date.today()

    async with get_session() as db:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            logger.error("User %s not found", user_id)
            return

        # --- Step 1: Resolve clarification session ---
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

        assumptions_log: list[str] = []
        if clarification_session:
            unanswered = [q for q in clarification_session.questions if q.answer is None]
            if unanswered:
                logger.info(
                    "%d unanswered question(s) for user %s — applying assumptions",
                    len(unanswered),
                    user_id,
                )
                assumptions_log = await apply_assumptions(db, clarification_session)
            else:
                clarification_session.status = "resolved"
                await db.commit()

        answered_context = (
            {q.trigger: q.answer for q in clarification_session.questions}
            if clarification_session
            else {}
        )

        # --- Step 2: Create MealPlan row (status=generating) ---
        meal_plan = MealPlan(
            user_id=user_id,
            plan_date=today,
            status="generating",
            assumptions_made="\n".join(assumptions_log) if assumptions_log else None,
        )
        db.add(meal_plan)
        await db.commit()
        await db.refresh(meal_plan)

    # --- Step 3: LLM generation (stub — wired in Step 3 of the project) ---
    logger.info(
        "LLM meal generation not yet implemented; answered_context=%s", answered_context
    )
    # TODO: Call the LLM orchestrator with:
    #   - user profile & preferences
    #   - today's classified calendar events
    #   - answered_context (clarification answers + assumptions)
    #   - agent memories
    #   - recent meal_history_index entries
    # Then populate meal_plan.meals and send via Resend.

    async with get_session() as db:
        result = await db.execute(select(MealPlan).where(MealPlan.id == meal_plan.id))
        plan = result.scalar_one()
        plan.status = "failed"  # will become "sent" once LLM step is wired
        plan.generated_at = datetime.now(timezone.utc)
        await db.commit()

    logger.info("Morning run complete (LLM stub) for user %s", user_id)
