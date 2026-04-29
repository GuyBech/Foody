"""
Continuous Learning Engine — dietary memory management.

Two public responsibilities:
  1. load_dietary_profile()  — summarise all active AgentMemory rows into a
     prompt-ready string for the meal planner. The profile is entirely DB-driven;
     nothing is hardcoded here.

  2. consolidate_feedback()  — after a user rates a meal plan, run a fast
     Haiku LLM call to extract dietary learnings and write them as new
     AgentMemory rows. This is how the system gets smarter over time.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from foody.agent.schemas import MEMORY_UPDATE_TOOL, MemoryUpdateOutput
from foody.config import settings
from foody.db.models import AgentMemory, MealPlan

import uuid

logger = logging.getLogger(__name__)

_FEEDBACK_PROMPT_PATH = Path(__file__).parent / "prompts" / "consolidate_feedback.md"

# Haiku is fast and cheap — perfect for incremental memory updates.
_CONSOLIDATION_MODEL = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

async def load_dietary_profile(user_id: uuid.UUID, db: AsyncSession) -> str:
    """
    Load all active AgentMemory rows for a user and format them as a
    structured dietary profile block for LLM prompts.

    Returns a placeholder string when no memories exist yet so the planner
    can still operate with sensible defaults.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AgentMemory)
        .where(
            AgentMemory.user_id == user_id,
            or_(AgentMemory.expires_at.is_(None), AgentMemory.expires_at > now),
        )
        .order_by(AgentMemory.kind, AgentMemory.confidence.desc())
    )
    memories = result.scalars().all()

    if not memories:
        return (
            "No dietary memories recorded yet. Apply standard evidence-based "
            "meal planning principles and use the user profile targets above."
        )

    by_kind: dict[str, list[AgentMemory]] = {}
    for m in memories:
        by_kind.setdefault(m.kind, []).append(m)

    sections: list[str] = []
    kind_headings = {
        "preference": "Preferences (what works well)",
        "constraint": "Constraints (hard limits)",
        "habit": "Habits (regular patterns)",
        "observation": "Observations (context notes)",
    }

    for kind in ["constraint", "preference", "habit", "observation"]:
        items = by_kind.get(kind, [])
        if not items:
            continue
        sections.append(f"**{kind_headings.get(kind, kind.title())}**")
        for m in items:
            conf = f" _(confidence {m.confidence:.0%})_" if m.confidence < 0.85 else ""
            sections.append(f"- {m.content}{conf}")

    return "\n".join(sections)


async def load_meal_history(
    user_id: uuid.UUID,
    db: AsyncSession,
    days: int = 14,
) -> list[str]:
    """Return meal signatures suggested in the last `days` days, most recent first."""
    from datetime import timedelta
    from sqlalchemy import and_
    from foody.db.models import MealHistoryIndex

    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(MealHistoryIndex.meal_signature, MealHistoryIndex.last_suggested)
        .where(
            and_(
                MealHistoryIndex.user_id == user_id,
                MealHistoryIndex.last_suggested >= since,
            )
        )
        .order_by(MealHistoryIndex.last_suggested.desc())
    )
    return [row[0] for row in result.all()]


async def update_meal_history(
    user_id: uuid.UUID,
    titles: list[str],
    plan_date: date,
    db: AsyncSession,
) -> None:
    """Upsert meal signatures into MealHistoryIndex after a plan is generated."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from foody.db.models import MealHistoryIndex

    for title in titles:
        sig = title.lower().strip()[:120]
        stmt = (
            pg_insert(MealHistoryIndex)
            .values(user_id=user_id, meal_signature=sig, last_suggested=plan_date, times_30d=1)
            .on_conflict_do_update(
                index_elements=["user_id", "meal_signature"],
                set_={
                    "last_suggested": plan_date,
                    "times_30d": MealHistoryIndex.times_30d + 1,
                },
            )
        )
        await db.execute(stmt)
    await db.commit()


# ---------------------------------------------------------------------------
# Memory write helpers
# ---------------------------------------------------------------------------

async def save_memory(
    user_id: uuid.UUID,
    kind: str,
    content: str,
    confidence: float,
    source: str,
    db: AsyncSession,
) -> None:
    db.add(
        AgentMemory(
            user_id=user_id,
            kind=kind,
            content=content,
            confidence=confidence,
            source=source,
        )
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Feedback consolidation (the "learning" step)
# ---------------------------------------------------------------------------

async def consolidate_feedback(
    user_id: uuid.UUID,
    plan: MealPlan,
    rating: int,
    db: AsyncSession,
) -> None:
    """
    Run an LLM call (Haiku) to extract dietary learnings from a rated meal plan
    and write them as new AgentMemory rows.

    This is fire-and-forget from the perspective of the Telegram handler —
    any exception is logged but not re-raised.
    """
    if not plan.meals:
        return

    existing_profile = await load_dietary_profile(user_id, db)

    # Build a concise summary of what was suggested
    meal_lines = [
        f"- {m.slot.replace('_',' ').title()} ({m.suggested_time or '?'}): "
        f"{m.title} — {m.kcal} kcal, {m.protein_g}g P / {m.carbs_g}g C / {m.fat_g}g F"
        for m in sorted(plan.meals, key=lambda x: x.sequence)
    ]
    meals_summary = "\n".join(meal_lines)

    consolidation_prompt = _FEEDBACK_PROMPT_PATH.read_text(encoding="utf-8")
    user_message = (
        f"## Meal Plan for {plan.plan_date} — rated {rating}/5\n\n"
        f"{meals_summary}\n\n"
        f"## Existing Dietary Profile\n\n{existing_profile}"
    )

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=_CONSOLIDATION_MODEL,
            max_tokens=1024,
            system=consolidation_prompt,
            tools=[MEMORY_UPDATE_TOOL],
            tool_choice={"type": "tool", "name": "update_dietary_memories"},
            messages=[{"role": "user", "content": user_message}],
        )

        tool_block = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_block is None:
            logger.warning("Memory consolidation returned no tool_use block")
            return

        update = MemoryUpdateOutput.model_validate(tool_block.input)
        source_tag = f"feedback:{plan.plan_date}:{rating}/5"

        for mem in update.memories_to_add:
            db.add(
                AgentMemory(
                    user_id=user_id,
                    kind=mem.kind,
                    content=mem.content,
                    confidence=mem.confidence,
                    source=source_tag,
                )
            )
        await db.commit()

        logger.info(
            "Memory consolidation: added %d memories from rating %d/5 for plan %s",
            len(update.memories_to_add), rating, plan.plan_date,
        )
    except Exception:
        logger.exception("Memory consolidation failed — no memories updated")
