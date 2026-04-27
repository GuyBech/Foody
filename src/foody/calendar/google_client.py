"""
Google Calendar client.

Fetches events from one or more calendar IDs for a target date.
Events from different calendars are deduplicated by their composite key
"{calendar_id}::{event_id}" so shared events only appear once.

The google-api-python-client is synchronous; every call is wrapped in
asyncio.to_thread() to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build  # type: ignore[import-untyped]

from foody.config import settings


def _build_credentials(access_token: str, refresh_token: str) -> Credentials:
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _fetch_single_calendar_sync(
    creds: Credentials,
    calendar_id: str,
    target_date: date,
) -> list[dict[str, Any]]:
    """Synchronous fetch for one calendar. Returns raw Google API event dicts."""
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    response = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )

    events = response.get("items", [])
    # Tag each event with its source calendar and a composite deduplication key
    for event in events:
        event["_calendar_id"] = calendar_id
        event["_external_id"] = f"{calendar_id}::{event['id']}"
    return events


def _fetch_all_calendars_sync(
    access_token: str,
    refresh_token: str,
    target_date: date,
    calendar_ids: list[str],
) -> list[dict[str, Any]]:
    """Fetch events from all requested calendars and deduplicate by composite key."""
    creds = _build_credentials(access_token, refresh_token)

    seen: dict[str, dict[str, Any]] = {}
    for cal_id in calendar_ids:
        for event in _fetch_single_calendar_sync(creds, cal_id, target_date):
            composite_id = event["_external_id"]
            if composite_id not in seen:
                seen[composite_id] = event

    return list(seen.values())


async def fetch_events_for_calendars(
    access_token: str,
    refresh_token: str,
    target_date: date,
    calendar_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Async entry-point. Fetches and deduplicates events from all calendar IDs.
    Defaults to settings.calendar_id_list when calendar_ids is not provided.
    """
    ids = calendar_ids if calendar_ids is not None else settings.calendar_id_list
    return await asyncio.to_thread(
        _fetch_all_calendars_sync, access_token, refresh_token, target_date, ids
    )


def parse_event_times(event: dict[str, Any]) -> tuple[datetime, datetime, bool]:
    """Return (starts_at, ends_at, is_all_day) from a raw Google Calendar event dict."""
    start = event.get("start", {})
    end = event.get("end", {})

    if "date" in start:
        starts_at = datetime.fromisoformat(start["date"]).replace(tzinfo=timezone.utc)
        ends_at = datetime.fromisoformat(end["date"]).replace(tzinfo=timezone.utc)
        return starts_at, ends_at, True

    starts_at = datetime.fromisoformat(start["dateTime"])
    ends_at = datetime.fromisoformat(end["dateTime"])
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    return starts_at, ends_at, False
