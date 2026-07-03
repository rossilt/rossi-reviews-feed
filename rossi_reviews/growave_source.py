"""v1 source: full review objects (incl. body text) from the Growave API.

GATED ON PHASE 0 T3 (CLAUDE.md §6): the exact endpoint path, auth scheme,
pagination, and field names must be confirmed against the live interactive docs
(https://api.growave.io/v2/docs) with real credentials. The TODO(T3) markers are
the spots to fix. Until then the mapper reads the first matching key from a list
of plausible names so a rename degrades gracefully instead of crashing.

This module is the ONLY one that knows Growave's wire format."""
from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

from .models import Review

log = logging.getLogger(__name__)

# Candidate raw field names. T3 confirms the real ones; trim these lists after.
_PRODUCT_ID_KEYS = ("shopify_product_id", "product_id", "productId", "external_id")
_RATING_KEYS = ("rating", "score", "stars")
_BODY_KEYS = ("body", "description", "content", "text", "message", "review")
_TITLE_KEYS = ("title", "subject", "headline")
_AUTHOR_KEYS = ("author", "name", "customer_name", "reviewer", "first_name")
_LANG_KEYS = ("language", "lang", "locale")
_DATE_KEYS = ("created_at", "createdAt", "created", "date", "published_at")
_PUBLISHED_KEYS = ("published", "approved", "is_published", "is_approved", "state", "status")


def _first(raw: dict[str, Any], keys: Iterable[str]) -> Any:
    for k in keys:
        v = raw.get(k)
        if v is not None:
            return v
    return None


def _as_published(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True  # TODO(T3): confirm the approved flag; until then assume visible
    if isinstance(value, str):
        return value.strip().lower() in {
            "published", "approved", "active", "public", "1", "true", "yes",
        }
    return bool(value)


def map_raw_review(raw: dict[str, Any]) -> Review | None:
    """One raw Growave review -> internal Review; None if the join key or rating is
    missing or unparseable."""
    product_id = _first(raw, _PRODUCT_ID_KEYS)
    rating = _first(raw, _RATING_KEYS)
    if product_id is None or rating is None:
        return None
    try:
        return Review(
            product_id=product_id,
            rating=int(round(float(rating))),
            body=_first(raw, _BODY_KEYS) or "",
            title=_first(raw, _TITLE_KEYS),
            author=_first(raw, _AUTHOR_KEYS),
            language=_first(raw, _LANG_KEYS),  # TODO(T3): does Growave expose language at all? (§3)
            published=_as_published(_first(raw, _PUBLISHED_KEYS)),
            created_at=_first(raw, _DATE_KEYS),
        )
    except (ValueError, TypeError) as exc:
        log.warning("skipping unparseable review (%s): %r", exc, raw.get("id"))
        return None


class GrowaveSource:
    """Paginated fetch of all reviews. TODO(T3): path, auth headers, pagination
    params, response envelope — all to confirm against live docs."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        store_url: str,
        base_url: str = "https://api.growave.io/v2",
        *,
        page_size: int = 100,
        timeout: float = 30.0,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("GROWAVE_API_KEY and GROWAVE_API_SECRET are required for the v1 build")
        self.page_size = page_size
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                # TODO(T3): verify the real auth scheme
                "X-Api-Key": api_key,
                "X-Api-Secret": api_secret,
                "X-Shop-Url": store_url,
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GrowaveSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _extract_items(payload: Any) -> list[dict[str, Any]]:
        # TODO(T3): confirm envelope shape; the common ones handled for now.
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("reviews", "data", "items", "results"):
                v = payload.get(key)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict) and isinstance(v.get("items"), list):
                    return v["items"]
        return []

    def fetch_reviews(self) -> list[Review]:
        out: list[Review] = []
        page = 1
        while True:
            resp = self._client.get(
                "/reviews",  # TODO(T3): confirm path
                params={"page": page, "limit": self.page_size},  # TODO(T3): confirm params
            )
            resp.raise_for_status()
            items = self._extract_items(resp.json())
            if not items:
                break
            out.extend(rev for rev in (map_raw_review(x) for x in items) if rev is not None)
            if len(items) < self.page_size:
                break
            page += 1
        log.info("growave: fetched %d usable reviews", len(out))
        return out
