"""Internal data models. Sources (shopify_source, growave_source) map their wire
formats onto these; transform/emit never see a source-specific field name."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, field_validator


class Review(BaseModel):
    """One normalized review (v1 / Growave path). Join key: Shopify product id."""

    product_id: str
    rating: int
    body: str = ""
    title: str | None = None
    author: str | None = None
    language: str | None = None       # None = untagged, assumed store-default (LT)
    published: bool = True
    created_at: datetime | None = None

    @field_validator("product_id", mode="before")
    @classmethod
    def _coerce_product_id(cls, v: object) -> object:
        return str(v) if v is not None else v

    @field_validator("created_at")
    @classmethod
    def _ensure_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class ProductSummary(BaseModel):
    """Per-product record inside the feed's `products` dict (CLAUDE.md §8)."""

    product_id: str
    avg: float
    count: int
    stars: str
    featured_text: str | None = None
    featured_author: str | None = None
    featured_rating: int | None = None
    # v2 (CLAUDE.md §7/§8): per-language featured quotes for the non-default
    # markets. None = no quote in that language; emit drops the null keys so
    # ~1200 products don't carry six null fields each.
    featured_text_lv: str | None = None
    featured_author_lv: str | None = None
    featured_rating_lv: int | None = None
    featured_text_et: str | None = None
    featured_author_et: str | None = None
    featured_rating_et: int | None = None
