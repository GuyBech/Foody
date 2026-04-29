"""
Google Calendar client — service-account auth (see CLAUDE.md).

Loads `google_credentials.json` (or whatever `settings.google_service_account_json`
points at), builds read-only Calendar credentials, and fetches events from one
or more calendar IDs for a target date.

Calendars must be shared with the service-account email. Personal Gmail
calendars (e.g. user@gmail.com) cannot be read by a service account without
domain-wide delegation — those are logged and skipped, not raised.

The google-api-python-client is synchronous; every call is wrapped in
asyncio.to_thread() to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from foody.config import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _resolve_credentials_path(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        # Project root = parent of src/foody/calendar/
        project_root = Path(__file__).resolve().parents[3]
        p = project_root / raw
    if not p.exists():
        raise FileNotFoundError(
            f"Service-account file not found at {p}. "
            f"Set GOOGLE_SERVICE_ACCOUNT_JSON to either a file path (local dev) "
            f"or the JSON contents (Vercel/cloud)."
        )
    return p


def _build_service_account_creds() -> service_account.Credentials:
    """Load service-account credentials from JSON content or a file path.

    settings.google_service_account_json is overloaded: in cloud environments
    (Vercel) it holds the inline JSON content of the service-account key; locally
    it's a path to google_credentials.json. We disambiguate by checking whether
    the value looks like a JSON object.
    """
    raw = (settings.google_service_account_json or "").strip()
    if raw.startswith("{"):
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON looks like inline JSON but failed "
                f"to parse: {exc}. Paste the full key file contents into the env var."
            ) from exc
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)

    return service_account.Credentials.from_service_account_file(
        str(_resolve_credentials_path(raw or "google_credentials.json")),
        scopes=_SCOPES,
    )


def _fetch_single_calendar_sync(
    creds: service_account.Credentials,
    calendar_id: str,
    target_date: date,
) -> list[dict[str, Any]]:
    """Synchronous fetch for one calendar. Returns raw Google API event dicts.

    Returns an empty list (and logs a warning) if the service account is not
    authorized to read this calendar — that case is non-fatal.
    """
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    try:
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
    except HttpError as exc:
        # 403/404 typically mean the calendar isn't shared with the service
        # account, or it's a personal @gmail.com calendar. Skip rather than fail.
        if exc.resp.status in (403, 404):
            logger.warning(
                "Calendar %s unreachable (HTTP %s) — skipping. "
                "Share it with the service-account email if you want it included.",
                calendar_id, exc.resp.status,
            )
            return []
        raise

    events = response.get("items", [])
    for event in events:
        event["_calendar_id"] = calendar_id
        event["_external_id"] = f"{calendar_id}::{event['id']}"
    return events


def _fetch_all_calendars_sync(
    target_date: date,
    calendar_ids: list[str],
) -> list[dict[str, Any]]:
    """Fetch events from all requested calendars and deduplicate by composite key."""
    creds = _build_service_account_creds()

    seen: dict[str, dict[str, Any]] = {}
    for cal_id in calendar_ids:
        for event in _fetch_single_calendar_sync(creds, cal_id, target_date):
            composite_id = event["_external_id"]
            if composite_id not in seen:
                seen[composite_id] = event

    return list(seen.values())


async def fetch_events_for_calendars(
    access_token: str = "",
    refresh_token: str = "",
    target_date: date | None = None,
    calendar_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Async entry-point. Fetches and deduplicates events from all calendar IDs.

    `access_token` and `refresh_token` are accepted for backward compatibility
    with the old OAuth signature but are ignored — auth uses the service
    account file specified in settings.google_service_account_json.
    """
    if target_date is None:
        target_date = date.today() + timedelta(days=1)
    ids = calendar_ids if calendar_ids is not None else settings.calendar_id_list
    return await asyncio.to_thread(_fetch_all_calendars_sync, target_date, ids)


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
