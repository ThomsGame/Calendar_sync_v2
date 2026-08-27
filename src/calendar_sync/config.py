"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Snexi
    snexi_url: str = "https://snexi.fr/portail"
    snexi_username: str = ""
    snexi_password: str = ""
    snexi_headless: bool = True

    # Constatimmo
    constatimmo_url: str = "https://constatonline.constatimmo.com"
    constatimmo_username: str = ""
    constatimmo_password: str = ""
    constatimmo_user_data_dir: str = "./.browser/constatimmo"
    constatimmo_headless: bool = True

    # Google Calendar — OAuth2 user-consent flow
    google_calendar_id: str = "primary"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_token_path: str = "./token.json"
    google_calendar_os_id: str = ""
    google_calendar_odm_id: str = ""

    # Google Calendar event colors (1-11, Google Calendar palette)
    google_color_snexi: str = "5"
    google_color_constatimmo: str = "11"

    # Feature flags
    dry_run: bool = False
    snexi_enrich_details: bool = True
    constatimmo_enrich_details: bool = True

    @property
    def os_calendar_id(self) -> str:
        return self.google_calendar_os_id or self.google_calendar_id

    @property
    def odm_calendar_id(self) -> str:
        return self.google_calendar_odm_id or self.google_calendar_id


def load_settings() -> Settings:
    """Load settings from .env file."""
    return Settings()
