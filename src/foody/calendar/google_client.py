"""
Google Calendar client.

Fetches events for a given date using already-stored OAuth tokens.
Runs the synchronous google-api-python-client in a thread so it doesn't
block the async event loop.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build  # type: ignore[import-untyped]

from foody.config import settings


def _fetch_events_sync(
    access_token: str,
    refresh_token: str,
    target_date: date,
) -> list[dict[str, Any]]:
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
    )

    # Refresh token if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    response = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )

    return response.get("items", [])


async def fetch_events_for_date(
    access_token: str,
    refresh_token: str,
    target_date: date,
) -> list[dict[str, Any]]:
    """Async wrapper — runs the blocking API call in a thread pool."""
    return await asyncio.to_thread(
        _fetch_events_sync, access_token, refresh_token, target_date
    )


def parse_event_times(event: dict[str, Any]) -> tuple[datetime, datetime, bool]:
    """Return (starts_at, ends_at, is_all_day) from a raw Google Calendar event dict."""
    start = event.get("start", {})
    end = event.get("end", {})

    if "date" in start:
        # All-day event
        starts_at = datetime.fromisoformat(start["date"]).replace(tzinfo=timezone.utc)
        ends_at = datetime.fromisoformat(end["date"]).replace(tzinfo=timezone.utc)
        return starts_at, ends_at, True

    starts_at = datetime.fromisoformat(start["dateTime"])
    ends_at = datetime.fromisoformat(end["dateTime"])
    # Ensure timezone-aware
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    return starts_at, ends_at, False
