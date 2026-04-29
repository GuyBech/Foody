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
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from telegram import Update

from foody.config import settings
from foody.telegram.handlers import handle_callback_query

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _dbg(msg: str) -> None:
    """Vercel Python sometimes drops buffered logger output — print+flush is reliable."""
    print(f"[telegram_webhook] {msg}", flush=True)


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

        # Identify the update type for the logs without leaking message content
        update_type = "unknown"
        for candidate in ("callback_query", "message", "edited_message", "channel_post",
                         "edited_channel_post", "inline_query", "chosen_inline_result",
                         "shipping_query", "pre_checkout_query", "poll", "poll_answer",
                         "my_chat_member", "chat_member", "chat_join_request"):
            if update_data.get(candidate) is not None:
                update_type = candidate
                break
        _dbg(f"update_type={update_type} top_level_keys={list(update_data.keys())}")

        if update.callback_query:
            _dbg("dispatching to handle_callback_query")
            await handle_callback_query(update)
            _dbg("handle_callback_query returned")
        else:
            _dbg(f"no handler for update_type={update_type} — ignoring")


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        _dbg("do_POST entered")

        # Verify Telegram secret token
        secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
        secret_configured = bool(settings.telegram_webhook_secret)
        verified = _verify_secret(secret)
        _dbg(
            f"secret_check verified={verified} header_present={secret is not None} "
            f"secret_configured={secret_configured}"
        )
        if not verified:
            self._respond(403, {"error": "Forbidden"})
            _dbg("responded 403 (verification failed)")
            return

        # Read body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _dbg(f"body_bytes={length}")

        try:
            update_data = json.loads(body)
        except json.JSONDecodeError as exc:
            _dbg(f"json_decode_error: {exc}")
            self._respond(400, {"error": "Invalid JSON"})
            return

        try:
            asyncio.run(_dispatch(update_data))
        except Exception as exc:
            _dbg(f"dispatch_failed: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            # Always return 200 to Telegram so it doesn't retry
        finally:
            try:
                self._respond(200, {"ok": True})
                _dbg("responded 200")
            except Exception as exc:
                _dbg(f"respond_failed: {type(exc).__name__}: {exc}")
                traceback.print_exc()

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass
