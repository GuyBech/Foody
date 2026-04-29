"""
Creates a single dev user with a full profile.
Run: uv run python scripts/seed_dev_user.py

Prints the user UUID — set it as FOODY_USER_ID in your .env.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env so DEV_TELEGRAM_CHAT_ID and DATABASE_URL are visible to os.getenv
# and to pydantic-settings.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from foody.db.engine import get_session
from foody.db.models import User, UserProfile


async def main() -> None:
    user_id = uuid.uuid4()
    async with get_session() as db:
        user = User(
            id=user_id,
            email="dev@foody.local",
            full_name="Dev User",
            timezone="Asia/Jerusalem",
            locale="he-IL",
            telegram_chat_id=os.getenv("DEV_TELEGRAM_CHAT_ID"),
        )
        db.add(user)
        await db.flush()

        db.add(
            UserProfile(
                user_id=user_id,
                sex="male",
                height_cm=178,
                weight_kg=80,
                activity_level="athlete",
                goal="recomp",
                target_calories=2800,
                target_protein_g=200,
                target_carbs_g=280,
                target_fat_g=80,
                dietary_pattern="omnivore",
                cooking_skill="intermediate",
                notes="Intense functional fitness + weightlifting. No fish.",
            )
        )
        await db.commit()

    print(f"Dev user created: {user_id}")
    print(f"Add to .env:  FOODY_USER_ID={user_id}")


asyncio.run(main())
