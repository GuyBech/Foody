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

# Diagnostic instrumentation — Vercel was showing "using HTTP Handler" then
# silence, suggesting an import-time crash before do_POST is reachable.
import sys


def _dbg(msg: str) -> None:
    """Write to BOTH stdout and stderr — Vercel's Python runtime sometimes only
    captures one of them depending on how the function was invoked."""
    sys.stdout.write(f"DEBUG: {msg}\n")
    sys.stdout.flush()
    sys.stderr.write(f"DEBUG: {msg}\n")
    sys.stderr.flush()


_dbg("telegram_webhook module load — start")

import asyncio
import hmac
import json
import logging
import os
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from telegram import Update

    _dbg("imported telegram.Update")
    from foody.config import settings

    _dbg("imported foody.config.settings — telegram_bot_token configured: %s" % bool(
        settings.telegram_bot_token
    ))
    from foody.telegram.handlers import handle_callback_query

    _dbg("imported foody.telegram.handlers.handle_callback_query")
except Exception as exc:
    _dbg(f"Error encountered during module import: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    raise

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_dbg("telegram_webhook module load — complete")


def _verify_secret(provided: str | None) -> bool:
    """Constant-time comparison against TELEGRAM_WEBHOOK_SECRET."""
    expected = settings.telegram_webhook_secret
    if not expected:
        # Secret not configured — skip verification (dev only)
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _summarize_update(update_data: dict) -> str:
    """Pull the most relevant text/data from an update for diagnostic logging."""
    if "callback_query" in update_data:
        cq = update_data["callback_query"] or {}
        return f"callback_query data={cq.get('data')!r} from_id={cq.get('from', {}).get('id')}"
    if "message" in update_data:
        msg = update_data["message"] or {}
        return f"message text={msg.get('text')!r} from_id={msg.get('from', {}).get('id')}"
    if "edited_message" in update_data:
        msg = update_data["edited_message"] or {}
        return f"edited_message text={msg.get('text')!r}"
    return f"other top_level_keys={list(update_data.keys())}"


async def _dispatch(update_data: dict) -> None:
    """Route the incoming update to the correct handler."""
    from telegram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    async with bot:
        update = Update.de_json(update_data, bot)

        if update.callback_query:
            _dbg("dispatching to handle_callback_query")
            await handle_callback_query(update)
            _dbg("handle_callback_query returned")
        else:
            _dbg(f"no handler for update — top_level_keys={list(update_data.keys())}")


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            _dbg("do_POST entered")

            secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
            verified = _verify_secret(secret)
            _dbg(f"Secret verification {'Passed' if verified else 'Failed'}")
            if not verified:
                self._respond(403, {"error": "Forbidden"})
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            _dbg(f"body_bytes={length}")

            try:
                update_data = json.loads(body)
            except json.JSONDecodeError as exc:
                _dbg(f"Error encountered: JSONDecodeError: {exc}")
                self._respond(400, {"error": "Invalid JSON"})
                return

            _dbg(f"Processing message: {_summarize_update(update_data)}")

            try:
                asyncio.run(_dispatch(update_data))
            except Exception as exc:
                _dbg(f"Error encountered: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                # Always return 200 to Telegram so it doesn't retry
        except Exception as exc:
            # Last-resort guard so silent crashes don't escape diagnostics
            _dbg(f"Error encountered (outer): {type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            try:
                self._respond(200, {"ok": True})
                _dbg("responded 200")
            except Exception as exc:
                _dbg(f"Error encountered while responding: {type(exc).__name__}: {exc}")
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
