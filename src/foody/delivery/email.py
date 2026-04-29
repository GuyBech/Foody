"""
Email delivery via Resend + Jinja2 HTML templates.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader

from foody.agent.schemas import MealPlanOutput, SLOT_LABELS
from foody.config import settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_JINJA_ENV = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)

_SLOT_COLORS: dict[str, str] = {
    "breakfast": "#FEF9C3",
    "morning_snack": "#DBEAFE",
    "lunch": "#D1FAE5",
    "pre_workout": "#FFEDD5",
    "post_workout": "#FCE7F3",
    "dinner": "#EDE9FE",
    "evening_snack": "#F3F4F6",
}

_SLOT_ACCENT_COLORS: dict[str, str] = {
    "breakfast": "#D97706",
    "morning_snack": "#2563EB",
    "lunch": "#059669",
    "pre_workout": "#EA580C",
    "post_workout": "#DB2777",
    "dinner": "#7C3AED",
    "evening_snack": "#6B7280",
}

_SLOT_ICONS: dict[str, str] = {
    "breakfast": "☀️",
    "morning_snack": "🍎",
    "lunch": "🥗",
    "pre_workout": "⚡",
    "post_workout": "💪",
    "dinner": "🌙",
    "evening_snack": "🌿",
}


async def send_meal_plan_email(
    *,
    to_email: str,
    user_name: str,
    plan_date: date,
    plan: MealPlanOutput,
    assumptions: str | None = None,
    from_email: str = "Foody <plans@foody.app>",
) -> str:
    """Render the meal plan HTML template and deliver via Resend. Returns the Resend email ID."""
    template = _JINJA_ENV.get_template("morning_plan.html")

    meals_with_meta = [
        {
            "slot_label": SLOT_LABELS.get(m.slot, m.slot.replace("_", " ").title()),
            "slot_color": _SLOT_COLORS.get(m.slot, "#F9FAFB"),
            "slot_accent": _SLOT_ACCENT_COLORS.get(m.slot, "#6B7280"),
            "slot_icon": _SLOT_ICONS.get(m.slot, "🍽"),
            "time": m.suggested_time,
            "title": m.title,
            "description": m.description,
            "kcal": m.kcal,
            "protein_g": m.protein_g,
            "carbs_g": m.carbs_g,
            "fat_g": m.fat_g,
            "rationale": m.rationale,
            "context_tags": m.context_tags,
        }
        for m in plan.meals
    ]

    plan_date_str = f"{plan_date.strftime('%A, %B')} {plan_date.day}"
    html = template.render(
        user_name=user_name,
        plan_date=plan_date_str,
        summary=plan.summary,
        day_overview=plan.day_overview,
        meals=meals_with_meta,
        total_kcal=plan.total_kcal,
        total_protein_g=plan.total_protein_g,
        total_carbs_g=plan.total_carbs_g,
        total_fat_g=plan.total_fat_g,
        assumptions=assumptions,
    )

    resend.api_key = settings.resend_api_key
    params: resend.Emails.SendParams = {
        "from": from_email,
        "to": [to_email],
        "subject": f"🍽 Your meal plan for {plan_date_str}",
        "html": html,
    }
    response = resend.Emails.send(params)
    email_id = response.get("id", "") if isinstance(response, dict) else getattr(response, "id", "")
    logger.info("Email delivered to %s for %s (resend_id=%s)", to_email, plan_date, email_id)
    return str(email_id)
