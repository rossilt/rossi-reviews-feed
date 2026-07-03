"""v0 source: read the Growave-maintained `ssw.review` metafield off every product
via the Shopify Admin GraphQL API (CLAUDE.md §2 / §9).

Live-verified against rossi.lt (2026-07-03):
- metafield type is `json`, value like {"count":6,"avg":5,"product_id":6054761234637}
- values are INCONSISTENTLY TYPED across products — sometimes numbers, sometimes
  strings ({"count":"32","avg":"5",...}) — so parsing coerces both.
- `legacyResourceId` equals the embedded product_id (the join key).
- products without reviews have metafield: null.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterator

import httpx

from .models import ProductSummary
from .transform import summary_from_counts

log = logging.getLogger(__name__)

def fetch_access_token(
    shop: str, client_id: str, client_secret: str, *, timeout: float = 30.0
) -> str:
    """Exchange custom-app client credentials for a short-lived Admin API token
    (OAuth client_credentials grant, ~24h expiry — each run mints a fresh one).
    Live-verified against rossilietuva.myshopify.com 2026-07-03."""
    resp = httpx.post(
        f"https://{shop}/admin/oauth/access_token",
        json={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    log.info("minted admin token via client-credentials grant (scope: %s)", data.get("scope"))
    return data["access_token"]


PRODUCTS_QUERY = """
query ProductsReviewMeta($first: Int!, $cursor: String) {
  products(first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      legacyResourceId
      metafield(namespace: "ssw", key: "review") { value }
    }
  }
}
"""


def parse_review_metafield(node: dict[str, Any]) -> ProductSummary | None:
    """Map one GraphQL product node to a ProductSummary, or None if the product has
    no parseable review metafield. Handles the live-observed string/number mix."""
    product_id = str(node.get("legacyResourceId") or "")
    metafield = node.get("metafield")
    if not product_id or not metafield or not metafield.get("value"):
        return None
    try:
        data = json.loads(metafield["value"])
        count = int(str(data["count"]))
        avg = float(str(data["avg"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        log.warning("product %s: unparseable ssw.review %r (%s) — skipped",
                    product_id, metafield.get("value"), exc)
        return None

    embedded = str(data.get("product_id", product_id))
    if embedded != product_id:
        log.warning("product %s: metafield carries product_id %s — using legacyResourceId",
                    product_id, embedded)
    if count < 0 or not (0 <= avg <= 5):
        log.warning("product %s: implausible count=%s avg=%s — skipped", product_id, count, avg)
        return None
    return summary_from_counts(product_id, avg, count)


class ShopifySource:
    """Paginated reader over all products. Retries THROTTLED responses; Advanced-plan
    rate limits make ~6 pages of 250 a non-issue, but be polite anyway."""

    def __init__(
        self,
        shop: str,
        token: str,
        api_version: str = "2025-07",
        *,
        page_size: int = 250,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        if not shop or not token:
            raise ValueError("SHOPIFY_SHOP and SHOPIFY_ADMIN_TOKEN are required for the v0 build")
        self.url = f"https://{shop}/admin/api/{api_version}/graphql.json"
        self.page_size = page_size
        self.max_retries = max_retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ShopifySource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, variables: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.max_retries + 1):
            resp = self._client.post(self.url, json={"query": PRODUCTS_QUERY, "variables": variables})
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2.0))
                log.info("HTTP 429; retrying in %.1fs (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            payload = resp.json()
            errors = payload.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                log.info("GraphQL THROTTLED; retrying in 2s (attempt %d)", attempt)
                time.sleep(2.0)
                continue
            if errors:
                raise RuntimeError(f"Shopify GraphQL errors: {errors}")
            return payload["data"]
        raise RuntimeError(f"Shopify API still throttled after {self.max_retries} retries")

    def _iter_nodes(self) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            data = self._post({"first": self.page_size, "cursor": cursor})
            products = data["products"]
            yield from products["nodes"]
            page = products["pageInfo"]
            if not page["hasNextPage"]:
                return
            cursor = page["endCursor"]
            time.sleep(0.3)  # stay well under the cost limit

    def fetch_summaries(self) -> dict[str, ProductSummary]:
        """All products' review summaries, keyed by Shopify product id (string).
        Products without a parseable metafield are skipped (logged in aggregate)."""
        summaries: dict[str, ProductSummary] = {}
        scanned = 0
        for node in self._iter_nodes():
            scanned += 1
            summary = parse_review_metafield(node)
            if summary is not None:
                summaries[summary.product_id] = summary
        log.info("shopify: scanned %d products, %d have a review metafield", scanned, len(summaries))
        return summaries
