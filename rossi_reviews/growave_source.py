"""v1 source: full review objects (incl. body text) from the Growave API.

Contract live-verified against rossi.lt on 2026-07-03 (Phase 0 T3), spec at
https://api.growave.io/v2/docs/swagger.json:

- Auth: POST /oauth/getAccessToken {clientId, clientSecret,
  grantType: "client_credentials", scope: "read_review"} ->
  {accessToken, tokenType: "Bearer", expiresAt (~1h)}.
- List: GET /reviews/getReviews with Bearer token; offset pagination
  ({totalCount, currentOffset, perPage, items}).
- ReviewDataDTO: id, title (nullable), body, rate (1-5), images, votes,
  isPublished, isPinned, isVerifiedBuyer, createdAt (ISO), customer{email,...},
  product{id: Shopify product id, handle} (nullable!), customerDisplayName.
- There is NO language field (CLAUDE.md §3 confirmed) -> Review.language stays
  None and the transform treats it as store-default (LT).
- PII: raw payloads carry customer emails/phones — the mapper deliberately takes
  ONLY customerDisplayName; nothing else may leak into the public feed.

This module is the ONLY one that knows Growave's wire format."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .models import Review

log = logging.getLogger(__name__)


def map_raw_review(raw: dict[str, Any]) -> Review | None:
    """One raw Growave review -> internal Review. Returns None for reviews
    without a product association (shop reviews / deleted products)."""
    product = raw.get("product") or {}
    product_id = product.get("id")
    rate = raw.get("rate")
    if product_id is None or rate is None:
        return None
    try:
        return Review(
            product_id=product_id,
            rating=int(round(float(rate))),
            body=raw.get("body") or "",
            title=raw.get("title"),
            author=raw.get("customerDisplayName"),
            language=None,  # Growave exposes no review language
            published=bool(raw.get("isPublished", False)),
            created_at=raw.get("createdAt"),
        )
    except (ValueError, TypeError) as exc:
        log.warning("skipping unparseable review id=%r (%s)", raw.get("id"), exc)
        return None


class GrowaveSource:
    """Paginated fetch of all published product reviews. Re-authenticates on 401
    (tokens expire after ~1h) and backs off on 429."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.growave.io/v2",
        *,
        page_size: int = 100,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("GROWAVE_API_KEY and GROWAVE_API_SECRET are required for the v1 build")
        self._api_key = api_key
        self._api_secret = api_secret
        self.page_size = page_size
        self.max_retries = max_retries
        self._token: str | None = None
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GrowaveSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _authenticate(self) -> None:
        resp = self._client.post(
            "/oauth/getAccessToken",
            json={
                "clientId": self._api_key,
                "clientSecret": self._api_secret,
                "grantType": "client_credentials",
                "scope": "read_review",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["accessToken"]
        log.info("growave: token minted (expires %s)", data.get("expiresAt"))

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.max_retries + 1):
            if self._token is None:
                self._authenticate()
            resp = self._client.get(
                path, params=params, headers={"Authorization": f"Bearer {self._token}"}
            )
            if resp.status_code == 401:
                log.info("growave: token expired; re-authenticating (attempt %d)", attempt)
                self._token = None
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2.0))
                log.info("growave: 429, retrying in %.1fs (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"growave: still failing after {self.max_retries} retries: {path}")

    def fetch_reviews(self) -> list[Review]:
        """All published product reviews, mapped to internal Reviews."""
        out: list[Review] = []
        offset = 0
        skipped = 0
        total: int | None = None
        while True:
            payload = self._get(
                "/reviews/getReviews",
                params={
                    "onlyPublishedReviews": "true",
                    "onlyProductReviews": "true",
                    "withCustomerDisplayName": "true",
                    "sortingOption": "mostRecent",
                    "perPage": self.page_size,
                    "offset": offset,
                },
            )
            items = payload.get("items", [])
            total = payload.get("totalCount", total)
            if not items:
                break
            for raw in items:
                review = map_raw_review(raw)
                if review is None:
                    skipped += 1
                else:
                    out.append(review)
            offset += len(items)
            if total is not None and offset >= total:
                break
            time.sleep(0.15)  # be polite; rate limits are undocumented
        log.info(
            "growave: fetched %d reviews (%d skipped without product) of totalCount=%s",
            len(out), skipped, total,
        )
        return out
