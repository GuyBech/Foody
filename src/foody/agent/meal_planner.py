"""
Meal Planner Agent — daily meal plan generation via LLM.

Responsibilities:
1. Build a structured prompt from pre-loaded user profile, dietary memories,
   meal history, calendar events, and clarification answers.
2. Call Anthropic using MEAL_PLAN_TOOL with prompt caching on static instructions.
3. Return a MealPlanResult with the full plan output + token/cost/latency metadata.

Design note: this function deliberately takes NO AsyncSession. All DB loading
happens in morning_run.py before the call, so we never hold a DB connection
open during the 10-30 second LLM round-trip.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic

from foody.agent.schemas import MEAL_PLAN_TOOL, MealPlanOutput
from foody.config import settings
from foody.db.models import CalendarEvent, UserProfile

logger = logging.getLogger(__name__)

_PLAN_PROMPT_PATH = Path(__file__).parent / "prompts" / "plan_meals.md"
_PLAN_MODEL = "claude-haiku-4-5"

# Pricing per million tokens (claude-haiku-4-5)
_INPUT_PER_MTK = 1.00
_OUTPUT_PER_MTK = 5.00
_CACHE_WRITE_PER_MTK = 1.25
_CACHE_READ_PER_MTK = 0.10


@dataclass
class MealPlanResult:
    output: MealPlanOutput
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    estimated_cost_usd: float = field(init=False)

    def __post_init__(self) -> None:
        self.estimated_cost_usd = (
            self.input_tokens * _INPUT_PER_MTK / 1_000_000
            + self.output_tokens * _OUTPUT_PER_MTK / 1_000_000
            + self.cache_write_tokens * _CACHE_WRITE_PER_MTK / 1_000_000
            + self.cache_read_tokens * _CACHE_READ_PER_MTK / 1_000_000
        )


# ---------------------------------------------------------------------------
# Context block builders
# ---------------------------------------------------------------------------

def _build_profile_block(profile: Optional[UserProfile]) -> str:
    if profile is None:
        return "No user profile available — apply standard evidence-based targets."
    lines: list[str] = []
    if profile.target_calories:
        lines.append(
            f"Daily targets: {profile.target_calories} kcal | "
            f"{profile.target_protein_g}g protein / {profile.target_carbs_g}g carbs / {profile.target_fat_g}g fat"
        )
    if profile.dietary_pattern:
        lines.append(f"Dietary pattern: {profile.dietary_pattern}")
    if profile.activity_level:
        lines.append(f"Activity level: {profile.activity_level}")
    if profile.goal:
        lines.append(f"Goal: {profile.goal}")
    if profile.allergies:
        lines.append(f"Allergies (hard constraint): {', '.join(profile.allergies)}")
    if profile.dislikes:
        lines.append(f"Dislikes: {', '.join(profile.dislikes)}")
    if profile.favorites:
        lines.append(f"Favourites: {', '.join(profile.favorites)}")
    if profile.cooking_skill:
        lines.append(f"Cooking skill: {profile.cooking_skill}")
    if profile.kitchen_equipment:
        lines.append(f"Available equipment: {', '.join(profile.kitchen_equipment)}")
    if profile.height_cm and profile.weight_kg:
        lines.append(f"Height: {profile.height_cm:.0f} cm | Weight: {profile.weight_kg:.1f} kg")
    return "\n".join(lines) if lines else "No profile data available."


def _build_calendar_block(
    events: list[CalendarEvent],
    answered_context: dict[str, str],
    assumptions_log: list[str],
) -> str:
    if not events:
        event_lines = ["No calendar events found for today — use standard meal timing defaults."]
    else:
        event_lines = []
        for e in sorted(events, key=lambda x: x.starts_at):
            start = e.starts_at.strftime("%H:%M")
            end = e.ends_at.strftime("%H:%M")
            category = e.event_category or "unknown"
            intensity_str = f", intensity={e.intensity}" if e.intensity else ""
            location_str = f", at_home={e.is_at_home}" if e.is_at_home is not None else ""
            all_day_str = " (all-day)" if e.is_all_day else ""
            event_lines.append(
                f"- {start}–{end}{all_day_str}: {e.title or 'Untitled'} "
                f"[{category}{intensity_str}{location_str}]"
            )

    sections = ["\n".join(event_lines)]

    if answered_context:
        sections.append("\n**Clarification answers from user:**")
        for trigger, answer in answered_context.items():
            sections.append(f"- {trigger}: {answer}")

    if assumptions_log:
        sections.append("\n## Assumptions Made\n")
        for assumption in assumptions_log:
            sections.append(f"- {assumption}")

    return "\n".join(sections)


def _build_history_block(signatures: list[str]) -> str:
    if not signatures:
        return "No recent meal history — full variety is available."
    return "\n".join(f"- {sig}" for sig in signatures[:30])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def plan_meals(
    user_id: uuid.UUID,
    profile: Optional[UserProfile],
    events: list[CalendarEvent],
    answered_context: dict[str, str],
    assumptions_log: list[str],
    plan_date: date,
    dietary_profile: str,
    meal_history: list[str],
    model: str = _PLAN_MODEL,
) -> MealPlanResult:
    """
    Generate a complete daily meal plan.

    Static instructions in plan_meals.md are sent with cache_control so
    repeated calls within the same 5-minute window hit Anthropic's prompt
    cache (saves ~70% on input token cost).
    """
    full_prompt = _PLAN_PROMPT_PATH.read_text(encoding="utf-8")

    # Split static instructions (cacheable) from the dynamic placeholder section
    split_marker = "## Dynamic User Context"
    split_idx = full_prompt.find(split_marker)
    static_instructions = full_prompt[:split_idx].rstrip() if split_idx != -1 else full_prompt

    profile_block = _build_profile_block(profile)
    history_block = _build_history_block(meal_history)
    calendar_block = _build_calendar_block(events, answered_context, assumptions_log)

    dynamic_context = "\n\n".join([
        "## Dynamic User Context",
        f"### User Profile\n{profile_block}",
        f"### Learned Dietary Profile\n{dietary_profile}",
        f"### Recent Meal History (avoid repetition)\n{history_block}",
    ])

    day_of_week = f"{plan_date.strftime('%A, %B')} {plan_date.day}, {plan_date.year}"
    user_message = (
        f"Plan meals for {day_of_week} ({plan_date.isoformat()}).\n\n"
        f"## Today's Schedule\n\n{calendar_block}"
    )

    system_blocks = [
        {
            "type": "text",
            "text": static_instructions,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_context,
        },
    ]

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    t0 = time.monotonic()
    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_blocks,
        tools=[MEAL_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_meal_plan"},
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("Meal planner returned no tool_use block")

    output = MealPlanOutput.model_validate(tool_block.input)

    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    logger.info(
        "Meal plan generated: %d meals, %d kcal | tokens in=%d out=%d "
        "cache_read=%d cache_write=%d | latency=%dms | cost=$%.4f",
        len(output.meals), output.total_kcal,
        usage.input_tokens, usage.output_tokens,
        cache_read, cache_write,
        latency_ms,
        MealPlanResult(
            output=output, model=model,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            latency_ms=latency_ms,
        ).estimated_cost_usd,
    )

    return MealPlanResult(
        output=output,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        latency_ms=latency_ms,
    )
