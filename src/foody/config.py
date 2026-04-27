from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    anthropic_api_key: str
    google_oauth_client_id: str
    google_oauth_client_secret: str
    resend_api_key: str
    telegram_bot_token: str
    telegram_webhook_secret: str = ""
    app_base_url: str = "http://localhost:3000"
    encryption_key: str = ""
    log_level: str = "INFO"
    debug: bool = False

    # Comma-separated Google Calendar IDs to aggregate.
    # Use "primary" for the main calendar. Add shared/work calendars as needed.
    # Example: "primary,youremail@gmail.com,teamcal@group.calendar.google.com"
    google_calendar_ids: str = "primary"

    @property
    def async_database_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    @property
    def calendar_id_list(self) -> list[str]:
        return [cid.strip() for cid in self.google_calendar_ids.split(",") if cid.strip()]


settings = Settings()
