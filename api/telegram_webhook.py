"""
Vercel serverless handler for incoming Telegram webhook updates.

Setup (run once):
  curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
    -d "url=https://<your-vercel-domain>/api/telegram_webhook" \
    -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"

Telegram sends every user interaction here as an HTTP POST with a JSON body.
We verify the X-Telegram-Bot-Api-Secret-Token header, then dispatch to the
appropriate handler based on the update type.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from telegram import Update

from foody.config import settings
from foody.telegram.handlers import handle_callback_query

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _verify_secret(provided: str | None) -> bool:
    """Constant-time comparison against TELEGRAM_WEBHOOK_SECRET."""
    expected = settings.telegram_webhook_secret
    if not expected:
        # Secret not configured — skip verification (dev only)
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


async def _dispatch(update_data: dict) -> None:
    """Route the incoming update to the correct handler."""
    # Build a lightweight bot proxy just for de-serialisation
    from telegram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    async with bot:
        update = Update.de_json(update_data, bot)

        if update.callback_query:
            await handle_callback_query(update)
        else:
            logger.debug("Unhandled update type: %s", list(update_data.keys()))


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        # Verify Telegram secret token
        secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not _verify_secret(secret):
            self._respond(403, {"error": "Forbidden"})
            return

        # Read body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            update_data = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "Invalid JSON"})
            return

        try:
            asyncio.run(_dispatch(update_data))
        except Exception:
            logger.exception("Error dispatching Telegram update")
            # Always return 200 to Telegram so it doesn't retry
        finally:
            self._respond(200, {"ok": True})

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass
