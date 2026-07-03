"""Runtime configuration from environment / .env (locally) or Actions secrets (CI)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Shopify (v0 source). Auth: EITHER client id+secret (custom-app OAuth
    # client-credentials grant — a fresh 24h token is minted each run; preferred)
    # OR a static shpat_ admin token.
    shopify_shop: str = ""            # *.myshopify.com domain
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_admin_token: str = ""
    shopify_api_version: str = "2025-07"

    # Growave (v1 source)
    growave_api_key: str = ""
    growave_api_secret: str = ""
    growave_store_url: str = "rossi.lt"
    growave_base_url: str = "https://api.growave.io/v2"

    # Transform behaviour (CLAUDE.md §7)
    review_language: str | None = "lt"
    featured_min_len: int = 40
    featured_max_len: int = 200

    # Emit behaviour (CLAUDE.md §5.1 / §8)
    output_path: str = "docs/reviews.json"
    feed_wrapped: bool = True         # False = bare products dict (T2 fallback)
    collapse_guard_ratio: float = 0.5
    log_level: str = "INFO"

    @field_validator("review_language", mode="before")
    @classmethod
    def _blank_language_is_none(cls, v: object) -> str | None:
        if v is None:
            return None
        v = str(v).strip()
        return v or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
