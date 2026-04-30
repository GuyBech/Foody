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


# ---------------------------------------------------------------------------
# Meal Planning output schemas
# ---------------------------------------------------------------------------

MealSlot = Literal[
    "breakfast", "morning_snack", "lunch",
    "pre_workout", "post_workout", "dinner", "evening_snack",
]

SLOT_LABELS: dict[str, str] = {
    "breakfast": "Breakfast",
    "morning_snack": "Morning Snack",
    "lunch": "Lunch",
    "pre_workout": "Pre-Workout",
    "post_workout": "Post-Workout",
    "dinner": "Dinner",
    "evening_snack": "Evening Snack",
}


class MealOutput(BaseModel):
    slot: MealSlot
    suggested_time: str = Field(description="HH:MM format")
    title: str
    description: str = Field(description="What to eat and how to prepare it (2-3 sentences).")
    kcal: int
    protein_g: int
    carbs_g: int
    fat_g: int
    rationale: str = Field(description="Why this meal at this time (1 sentence).")
    context_tags: list[str] = Field(
        default_factory=list,
        description="e.g. ['post_workout','quick_prep','high_protein','at_home']",
    )


class MealPlanOutput(BaseModel):
    summary: str = Field(description="One sentence framing the nutritional theme of the day.")
    meals: list[MealOutput]
    total_kcal: int
    total_protein_g: int
    total_carbs_g: int
    total_fat_g: int
    day_overview: str = Field(description="Key nutritional decisions and their reasoning (2-3 sentences).")


MEAL_PLAN_TOOL: dict = {
    "name": "submit_meal_plan",
    "description": "Submit the complete daily meal plan with macro-accurate meals timed to the schedule.",
    "input_schema": {
        "type": "object",
        "required": ["summary", "meals", "total_kcal", "total_protein_g", "total_carbs_g", "total_fat_g", "day_overview"],
        "properties": {
            "summary": {"type": "string", "description": "One-sentence nutritional theme."},
            "meals": {
                "type": "array",
                "minItems": 3,
                "items": {
                    "type": "object",
                    "required": ["slot", "suggested_time", "title", "description", "kcal", "protein_g", "carbs_g", "fat_g", "rationale"],
                    "properties": {
                        "slot": {
                            "type": "string",
                            "enum": ["breakfast","morning_snack","lunch","pre_workout","post_workout","dinner","evening_snack"],
                        },
                        "suggested_time": {"type": "string", "description": "HH:MM"},
                        "title": {"type": "string"},
                        "description": {"type": "string", "description": "What to eat and a brief prep note."},
                        "kcal": {"type": "integer"},
                        "protein_g": {"type": "integer"},
                        "carbs_g": {"type": "integer"},
                        "fat_g": {"type": "integer"},
                        "rationale": {"type": "string", "description": "Why this meal at this time."},
                        "context_tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "total_kcal": {"type": "integer"},
            "total_protein_g": {"type": "integer"},
            "total_carbs_g": {"type": "integer"},
            "total_fat_g": {"type": "integer"},
            "day_overview": {"type": "string", "description": "Key nutritional decisions (2-3 sentences)."},
        },
    },
}


# ---------------------------------------------------------------------------
# Memory consolidation schemas  (used by memory.py after feedback)
# ---------------------------------------------------------------------------

class MemoryToAdd(BaseModel):
    kind: Literal["preference", "constraint", "habit", "observation"]
    content: str = Field(description="A single, specific, actionable fact about this user's dietary preferences.")
    confidence: float = Field(ge=0.0, le=1.0)


class MemoryUpdateOutput(BaseModel):
    memories_to_add: list[MemoryToAdd]
    summary: str = Field(description="1-2 sentence summary of what was learned from this feedback.")


MEMORY_UPDATE_TOOL: dict = {
    "name": "update_dietary_memories",
    "description": "Extract dietary learnings from a meal plan feedback rating and return structured memory updates.",
    "input_schema": {
        "type": "object",
        "required": ["memories_to_add", "summary"],
        "properties": {
            "memories_to_add": {
                "type": "array",
                "description": "New facts to add to the user's dietary memory. Keep each entry specific and actionable.",
                "items": {
                    "type": "object",
                    "required": ["kind", "content", "confidence"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["preference", "constraint", "habit", "observation"],
                        },
                        "content": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                },
            },
            "summary": {"type": "string"},
        },
    },
}


# ---------------------------------------------------------------------------
# Leftovers / batch-cooking — read-only context the meal planner consults.
# Not an LLM tool output (the LLM does not write leftovers); just a typed
# representation of rows pulled from the leftovers table.
# ---------------------------------------------------------------------------

class LeftoverItem(BaseModel):
    item_description: str
    is_active: bool = True
