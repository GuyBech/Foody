"""
Vercel serverless handler for the evening prep cron.
Schedule: 0 17 * * *  (17:00 UTC = ~20:00 Israel time)
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler

# Make the src/ package importable in the Vercel runtime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from foody.jobs.evening_prep import run_evening_prep

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._run()

    def do_POST(self) -> None:
        self._run()

    def _run(self) -> None:
        try:
            # In MVP there is a single user; user_id is injected via env var.
            # Future: loop over all active users.
            raw_user_id = os.getenv("FOODY_USER_ID", "")
            if not raw_user_id:
                self._respond(500, {"error": "FOODY_USER_ID env var not set"})
                return

            user_id = uuid.UUID(raw_user_id)
            asyncio.run(run_evening_prep(user_id))
            self._respond(200, {"status": "ok"})
        except Exception as exc:
            logger.exception("Evening prep failed")
            self._respond(500, {"error": str(exc)})

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress default BaseHTTPRequestHandler access log
