"""
LLM-powered calendar event classifier.

Sends all events for a target date to Claude in a single API call using
tool_use to force structured output. Returns EventClassification objects
that the evening_prep job uses to build clarification questions.

Design choices:
- System prompt is sent with cache_control="ephemeral" so the static
  user context (profile + instructions) is reused across calls. This
  cuts token cost by ~70% on repeated runs.
- tool_choice forces the model to call submit_event_classifications,
  making JSON parsing deterministic.
- Falls back gracefully: the caller catches any exception and falls back
  to rule-based classification if the LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import anthropic

from foody.agent.schemas import CLASSIFICATION_TOOL, ClassificationBatch, EventClassification
from foody.config import settings
from foody.db.models import CalendarEvent, UserProfile

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "classify_calendar.md"

# Approximate cost per million tokens for claude-sonnet-4-6 (USD).
# Used for the agent_runs.cost_usd estimate only — not for billing.
_INPUT_COST_PER_M = 3.00
_OUTPUT_COST_PER_M = 15.00
_CACHE_READ_COST_PER_M = 0.30


@dataclass
class ClassificationResult:
    classifications: list[EventClassification]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    estimated_cost_usd: float = field(init=False)

    def __post_init__(self) -> None:
        self.estimated_cost_usd = round(
            (self.input_tokens / 1_000_000) * _INPUT_COST_PER_M
            + (self.output_tokens / 1_000_000) * _OUTPUT_COST_PER_M
            + (self.cache_read_tokens / 1_000_000) * _CACHE_READ_COST_PER_M,
            6,
        )


def _load_system_prompt(profile: UserProfile | None) -> str:
    """Load the .md template and inject profile-specific macro targets."""
    text = _PROMPT_PATH.read_text(encoding="utf-8")
    replacements: dict[str, str] = {
        "<<TARGET_CALORIES>>": str(getattr(profile, "target_calories", None) or 2800),
        "<<TARGET_PROTEIN_G>>": str(getattr(profile, "target_protein_g", None) or 200),
        "<<TARGET_CARBS_G>>": str(getattr(profile, "target_carbs_g", None) or 280),
        "<<TARGET_FAT_G>>": str(getattr(profile, "target_fat_g", None) or 80),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return text


def _format_events_for_prompt(events: list[CalendarEvent], plan_date: date) -> str:
    """Serialise CalendarEvent ORM objects into a concise JSON string for the prompt."""
    items: list[dict[str, Any]] = []
    for e in events:
        items.append(
            {
                "event_id": e.external_id,
                "title": e.title or "(no title)",
                "description": (e.description or "")[:300],  # truncate long descriptions
                "location": e.location,
                "starts_at": e.starts_at.strftime("%H:%M") if not e.is_all_day else "all-day",
                "ends_at": e.ends_at.strftime("%H:%M") if not e.is_all_day else "all-day",
                "is_all_day": e.is_all_day,
            }
        )
    return json.dumps({"plan_date": str(plan_date), "events": items}, ensure_ascii=False, indent=2)


async def classify_calendar_events(
    events: list[CalendarEvent],
    profile: UserProfile | None,
    plan_date: date,
    model: str = "claude-sonnet-4-6",
) -> ClassificationResult:
    """
    Classify all events in a single LLM call and return structured results.

    Raises on API errors — the caller should catch and fall back to rule-based logic.
    """
    if not events:
        return ClassificationResult(
            classifications=[], model=model,
            input_tokens=0, output_tokens=0,
        )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    system_prompt = _load_system_prompt(profile)
    events_payload = _format_events_for_prompt(events, plan_date)

    user_message = (
        f"Please classify the following {len(events)} calendar event(s) "
        f"for {plan_date.strftime('%A, %B %-d')}.\n\n"
        f"{events_payload}"
    )

    t0 = time.monotonic()
    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        # System prompt with cache_control — the static content (instructions, keywords,
        # examples) will be cached after the first call. Only the user message varies daily.
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[CLASSIFICATION_TOOL],
        # Force the model to call exactly our tool — makes parsing deterministic
        tool_choice={"type": "tool", "name": "submit_event_classifications"},
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = int((time.monotonic() - t0) * 1000)

    # Extract the tool_use block (guaranteed by tool_choice="tool")
    tool_block = next(
        (b for b in response.content if b.type == "tool_use"), None
    )
    if tool_block is None:
        raise ValueError("Anthropic response contained no tool_use block")

    batch = ClassificationBatch.model_validate(tool_block.input)

    # Warn if the LLM skipped any events (shouldn't happen with the forced tool call)
    returned_ids = {c.event_id for c in batch.classifications}
    input_ids = {e.external_id for e in events}
    missing = input_ids - returned_ids
    if missing:
        logger.warning(
            "LLM did not classify %d event(s): %s", len(missing), missing
        )

    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    logger.info(
        "Calendar classification: %d events, %d input tokens (%d cached), "
        "%d output tokens, %dms",
        len(events),
        usage.input_tokens,
        cache_read,
        usage.output_tokens,
        latency_ms,
    )

    return ClassificationResult(
        classifications=batch.classifications,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        latency_ms=latency_ms,
    )
