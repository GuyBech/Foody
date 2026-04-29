"""
Telegram delivery smoke test — Option B from the dev test plan.

Bypasses Postgres, Google Calendar, and the LLM classifier. Hand-crafts
a realistic set of clarification questions for tomorrow and sends them
to the developer's chat via the real `send_evening_digest` function.

Run:
    python scripts/smoke_evening_digest.py

Reads from .env:
    TELEGRAM_BOT_TOKEN     – the bot to send from
    DEV_TELEGRAM_CHAT_ID   – the chat to send to
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

# --- Load .env so TELEGRAM_BOT_TOKEN / DEV_TELEGRAM_CHAT_ID are available ---
ROOT = Path(__file__).resolve().parent.parent
env_path = ROOT / ".env"
if env_path.exists():
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

# --- Inject dummy values for the Settings fields we don't actually use ---
# (foody.config.Settings declares these as required.)
os.environ.setdefault("DATABASE_URL", "postgresql://stub:stub@localhost:5432/stub")
os.environ.setdefault("ANTHROPIC_API_KEY", "stub")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "stub")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "stub")

# Make src/ importable
sys.path.insert(0, str(ROOT / "src"))

from foody.telegram.bot import send_evening_digest  # noqa: E402


def fake_question(
    sequence: int,
    question_text: str,
    question_type: str = "yes_no",
    options: dict | None = None,
) -> SimpleNamespace:
    """Stand-in for a ClarificationQuestion ORM row."""
    return SimpleNamespace(
        sequence=sequence,
        question_text=question_text,
        question_type=question_type,
        options=options,
        answer=None,
    )


async def main() -> None:
    chat_id = os.getenv("DEV_TELEGRAM_CHAT_ID")
    if not chat_id:
        raise SystemExit("DEV_TELEGRAM_CHAT_ID is not set in .env")

    plan_date = date.today() + timedelta(days=1)

    questions = [
        fake_question(
            sequence=1,
            question_text='Your 06:30 session "CrossFit @ The Yard" – what\'s the intensity?',
            question_type="choice",
            options={"choices": ["Light", "Moderate", "Intense", "Skip it"]},
        ),
        fake_question(
            sequence=2,
            question_text='"Software Engineering Lecture (TAU)" at 10:00 – will you eat on campus?',
            question_type="yes_no",
        ),
        fake_question(
            sequence=3,
            question_text='"Meeting" at 15:00 – is this a work call from home or commuting somewhere?',
            question_type="choice",
            options={"choices": ["Home", "Commuting", "Skip lunch"]},
        ),
    ]

    print(f"Sending evening digest for {plan_date} to chat {chat_id} ...")
    msg_id = await send_evening_digest(
        chat_id=chat_id,
        plan_date=plan_date,
        questions=questions,
    )
    print(f"OK — Telegram message_id={msg_id}")


if __name__ == "__main__":
    asyncio.run(main())
