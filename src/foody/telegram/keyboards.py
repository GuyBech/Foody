"""
Inline keyboard builders for the evening digest and meal plan feedback.

Callback data formats (both kept well under the 64-byte Telegram limit):
  Clarification  →  q:{sequence}:{answer_code}
    - yes_no      answer_code: "y" or "n"
    - choice      answer_code: 0-based index of chosen option
  Feedback       →  fb:{YYYY-MM-DD}:{1-5}
    e.g. fb:2026-04-27:4  (max 19 bytes)
"""

from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from foody.db.models import ClarificationQuestion


# ---------------------------------------------------------------------------
# Clarification keyboard
# ---------------------------------------------------------------------------

def _cb(sequence: int, answer_code: str) -> str:
    return f"q:{sequence}:{answer_code}"


def _question_buttons(q: ClarificationQuestion) -> list[InlineKeyboardButton] | None:
    """Return the row of buttons for one unanswered question, or None if answered."""
    if q.answer is not None:
        return None

    if q.question_type == "yes_no":
        return [
            InlineKeyboardButton("✅ Yes", callback_data=_cb(q.sequence, "y")),
            InlineKeyboardButton("❌ No", callback_data=_cb(q.sequence, "n")),
        ]

    if q.question_type == "choice" and q.options:
        choices: list[str] = q.options.get("choices", [])
        return [
            InlineKeyboardButton(label, callback_data=_cb(q.sequence, str(i)))
            for i, label in enumerate(choices)
        ]

    return None


def build_digest_keyboard(
    questions: list[ClarificationQuestion],
) -> InlineKeyboardMarkup | None:
    """Build an InlineKeyboardMarkup with one row per unanswered question."""
    rows = [row for q in questions if (row := _question_buttons(q)) is not None]
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def resolve_answer(q: ClarificationQuestion, answer_code: str) -> str:
    """Convert a callback answer_code back to the human-readable answer string."""
    if q.question_type == "yes_no":
        return "Yes" if answer_code == "y" else "No"

    if q.question_type == "choice" and q.options:
        choices: list[str] = q.options.get("choices", [])
        try:
            return choices[int(answer_code)]
        except (ValueError, IndexError):
            return answer_code

    return answer_code


# ---------------------------------------------------------------------------
# Feedback keyboard
# ---------------------------------------------------------------------------

def build_feedback_keyboard(plan_date: date) -> InlineKeyboardMarkup:
    """Star-rating row for a delivered meal plan. One tap sets overall_rating."""
    date_str = plan_date.isoformat()
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"fb:{date_str}:{rating}")
        for rating, label in [
            (1, "1 ★"), (2, "2 ★"), (3, "3 ★"), (4, "4 ★"), (5, "5 ★"),
        ]
    ]])
