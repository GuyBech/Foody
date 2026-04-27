"""
Inline keyboard builders for the evening clarification digest.

Callback data format: q:{sequence}:{answer_code}
  - yes_no   → answer_code is "y" or "n"
  - choice   → answer_code is the 0-based index of the chosen option

Max callback_data length is 64 bytes; this format stays well under that limit.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from foody.db.models import ClarificationQuestion


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
