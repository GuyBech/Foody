"""
Vercel serverless handler for the morning run cron.
Schedule: 0 3 * * *  (03:00 UTC = 06:00 Israel time)
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from foody.jobs.morning_run import run_morning_run

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._run()

    def do_POST(self) -> None:
        self._run()

    def _run(self) -> None:
        try:
            raw_user_id = os.getenv("FOODY_USER_ID", "")
            if not raw_user_id:
                self._respond(500, {"error": "FOODY_USER_ID env var not set"})
                return

            user_id = uuid.UUID(raw_user_id)
            asyncio.run(run_morning_run(user_id))
            self._respond(200, {"status": "ok"})
        except Exception as exc:
            logger.exception("Morning run failed")
            self._respond(500, {"error": str(exc)})

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass
