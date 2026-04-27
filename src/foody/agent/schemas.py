"""
Pydantic models and the Anthropic tool definition for calendar event classification.

EventClassification is the structured output the LLM produces for each event.
CLASSIFICATION_TOOL is the tool schema passed to the Anthropic API to force
structured output via tool_use.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

EventCategory = Literal[
    "workout", "study", "work", "commute", "meal", "military", "personal", "unknown"
]

WorkoutType = Literal[
    "crossfit_wod",
    "hyrox",
    "olympic_weightlifting",
    "strength",
    "cardio",
    "yoga",
    "run",
    "swim",
    "other",
]

Intensity = Literal["none", "low", "moderate", "high", "extreme"]

NutritionalFlag = Literal[
    "pre_workout_needed",
    "post_workout_needed",
    "pre_and_post_needed",
    "normal",
    "meal_at_risk",
]

ClarificationTrigger = Literal[
    "workout_intensity",
    "workout_type",
    "commute_time",
    "location",
    "schedule_conflict",
    "military_duration",
]


class EventClassification(BaseModel):
    event_id: str = Field(description="The event's external_id exactly as provided in the input.")
    category: EventCategory
    workout_type: Optional[WorkoutType] = Field(
        default=None, description="Set only when category=workout."
    )
    intensity: Intensity = "none"
    is_at_home: Optional[bool] = Field(
        default=None, description="null when genuinely unknown."
    )
    requires_commute: bool = False
    estimated_commute_minutes: Optional[int] = Field(
        default=None, description="One-way commute estimate in minutes; null if not applicable."
    )
    nutritional_flag: NutritionalFlag = "normal"
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_needed: bool = False
    clarification_trigger: Optional[ClarificationTrigger] = None
    suggested_question: Optional[str] = Field(
        default=None, description="The exact question to send the user."
    )
    suggested_question_type: Optional[Literal["yes_no", "choice"]] = None
    suggested_choices: Optional[list[str]] = Field(
        default=None, description="2–4 options for choice questions."
    )
    suggested_assumption: Optional[str] = Field(
        default=None, description="What the agent will assume if the user doesn't reply."
    )
    reasoning: str = Field(description="1-2 sentence rationale for the classification.")


class ClassificationBatch(BaseModel):
    classifications: list[EventClassification]


# ---------------------------------------------------------------------------
# Anthropic tool definition — kept manually in sync with EventClassification
# to avoid JSON Schema $ref resolution issues with some API versions.
# ---------------------------------------------------------------------------

CLASSIFICATION_TOOL: dict = {
    "name": "submit_event_classifications",
    "description": (
        "Submit structured nutritional classifications for ALL calendar events in the batch. "
        "Include every event provided, even all-day events and events with no nutritional impact."
    ),
    "input_schema": {
        "type": "object",
        "required": ["classifications"],
        "properties": {
            "classifications": {
                "type": "array",
                "description": "One classification object per input event.",
                "items": {
                    "type": "object",
                    "required": [
                        "event_id",
                        "category",
                        "intensity",
                        "requires_commute",
                        "nutritional_flag",
                        "confidence",
                        "clarification_needed",
                        "reasoning",
                    ],
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "Exact external_id from the input.",
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "workout", "study", "work", "commute",
                                "meal", "military", "personal", "unknown",
                            ],
                        },
                        "workout_type": {
                            "type": "string",
                            "enum": [
                                "crossfit_wod", "hyrox", "olympic_weightlifting",
                                "strength", "cardio", "yoga", "run", "swim", "other",
                            ],
                            "description": "Only set when category=workout.",
                        },
                        "intensity": {
                            "type": "string",
                            "enum": ["none", "low", "moderate", "high", "extreme"],
                        },
                        "is_at_home": {
                            "type": "boolean",
                            "description": "Omit when genuinely unknown.",
                        },
                        "requires_commute": {"type": "boolean"},
                        "estimated_commute_minutes": {
                            "type": "integer",
                            "description": "One-way commute in minutes. Omit if not applicable.",
                        },
                        "nutritional_flag": {
                            "type": "string",
                            "enum": [
                                "pre_workout_needed",
                                "post_workout_needed",
                                "pre_and_post_needed",
                                "normal",
                                "meal_at_risk",
                            ],
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "clarification_needed": {"type": "boolean"},
                        "clarification_trigger": {
                            "type": "string",
                            "enum": [
                                "workout_intensity",
                                "workout_type",
                                "commute_time",
                                "location",
                                "schedule_conflict",
                                "military_duration",
                            ],
                        },
                        "suggested_question": {
                            "type": "string",
                            "description": "Exact question text to send the user.",
                        },
                        "suggested_question_type": {
                            "type": "string",
                            "enum": ["yes_no", "choice"],
                        },
                        "suggested_choices": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 5,
                        },
                        "suggested_assumption": {
                            "type": "string",
                            "description": "Default if user doesn't reply.",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "1-2 sentences explaining the classification.",
                        },
                    },
                },
            }
        },
    },
}
