from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from foody.db.models import ClarificationQuestion, ClarificationSession


async def get_session_for_date(
    db: AsyncSession,
    user_id: uuid.UUID,
    plan_date: date,
) -> ClarificationSession | None:
    result = await db.execute(
        select(ClarificationSession)
        .where(
            ClarificationSession.user_id == user_id,
            ClarificationSession.plan_date == plan_date,
        )
        .options(selectinload(ClarificationSession.questions))
    )
    return result.scalar_one_or_none()


async def get_session_by_id(
    db: AsyncSession,
    session_id: uuid.UUID,
) -> ClarificationSession | None:
    result = await db.execute(
        select(ClarificationSession)
        .where(ClarificationSession.id == session_id)
        .options(selectinload(ClarificationSession.questions))
    )
    return result.scalar_one_or_none()


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    plan_date: date,
    questions: list[dict],
    expires_at: datetime,
) -> ClarificationSession:
    """Create a clarification session with all its questions in one transaction."""
    session = ClarificationSession(
        user_id=user_id,
        plan_date=plan_date,
        status="pending",
        expires_at=expires_at,
    )
    db.add(session)
    await db.flush()  # populate session.id before creating children

    for i, q in enumerate(questions):
        db.add(
            ClarificationQuestion(
                session_id=session.id,
                trigger=q["trigger"],
                question_text=q["question_text"],
                question_type=q.get("question_type", "yes_no"),
                options=q.get("options"),
                context=q.get("context"),
                assumption=q.get("assumption"),
                sequence=i,
            )
        )

    await db.commit()
    await db.refresh(session)
    # Reload questions after commit
    result = await db.execute(
        select(ClarificationSession)
        .where(ClarificationSession.id == session.id)
        .options(selectinload(ClarificationSession.questions))
    )
    return result.scalar_one()


async def record_answer(
    db: AsyncSession,
    session: ClarificationSession,
    sequence: int,
    answer: str,
) -> ClarificationQuestion:
    """Persist a user's answer and update session status."""
    question = next((q for q in session.questions if q.sequence == sequence), None)
    if question is None:
        raise ValueError(f"No question at sequence {sequence} in session {session.id}")

    question.answer = answer
    question.answered_at = datetime.now(timezone.utc)

    all_answered = all(q.answer is not None for q in session.questions)
    session.status = "fully_answered" if all_answered else "partially_answered"

    await db.commit()
    await db.refresh(question)
    return question


async def set_telegram_message_id(
    db: AsyncSession,
    session: ClarificationSession,
    message_id: int,
) -> None:
    session.telegram_message_id = message_id
    session.sent_at = datetime.now(timezone.utc)
    session.status = "pending"
    await db.commit()


async def apply_assumptions(
    db: AsyncSession,
    session: ClarificationSession,
) -> list[str]:
    """Fill unanswered questions with their default assumptions. Returns assumption strings."""
    applied: list[str] = []
    now = datetime.now(timezone.utc)

    for question in session.questions:
        if question.answer is None and question.assumption:
            question.answer = question.assumption
            question.answered_at = now
            applied.append(f"'{question.question_text}' → assumed: {question.assumption}")

    session.status = "expired"
    await db.commit()
    return applied
