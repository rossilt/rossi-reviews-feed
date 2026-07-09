"""Build + publish the reviews feed (CLAUDE.md §5.1).

    python -m rossi_reviews.build                      # auto: v1 (metafields+quotes) if Growave creds present, else v0
    python -m rossi_reviews.build --source shopify     # v0 stars-only, force
    python -m rossi_reviews.build --source full        # v1, fail if Growave creds missing
    python -m rossi_reviews.build --source fixture --fixture tests/fixtures/product_nodes.json
    python -m rossi_reviews.build --flat               # bare dict, no root wrapper (T2 fallback)
    python -m rossi_reviews.build --force              # bypass the collapse guard

Exit codes: 0 ok · 2 collapse guard tripped (old feed kept) · 1 other error.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import get_settings
from .emit import CollapseError, publish
from .growave_source import GrowaveSource
from .models import ProductSummary
from .shopify_source import ShopifySource, fetch_access_token, parse_review_metafield
from .transform import build_summaries

log = logging.getLogger(__name__)


def summaries_from_shopify() -> dict[str, ProductSummary]:
    s = get_settings()
    # Prefer the client-credentials exchange (always-fresh token); a static
    # shpat_ token is the fallback for setups without client creds.
    if s.shopify_client_id and s.shopify_client_secret:
        token = fetch_access_token(s.shopify_shop, s.shopify_client_id, s.shopify_client_secret)
    else:
        token = s.shopify_admin_token
    with ShopifySource(s.shopify_shop, token, s.shopify_api_version) as src:
        return src.fetch_summaries()


def summaries_from_growave() -> dict[str, ProductSummary]:
    """v1: full per-review pipeline — fetch, group, aggregate, pick featured quote."""
    s = get_settings()
    with GrowaveSource(s.growave_api_key, s.growave_api_secret, s.growave_base_url) as src:
        reviews = src.fetch_reviews()
    return build_summaries(
        reviews,
        language=s.review_language,
        min_len=s.featured_min_len,
        max_len=s.featured_max_len,
    )


def merge_quotes(
    base: dict[str, ProductSummary], quotes: dict[str, ProductSummary]
) -> dict[str, ProductSummary]:
    """Attach Growave featured quotes onto the metafield summaries.

    count/avg/stars stay METAFIELD-authoritative (live-proven to match the site);
    Growave contributes only the featured_* fields. The §5.1 sanity cross-check
    of the two aggregates runs every build and is logged, never fatal. Products
    with reviews only in Growave (no parseable metafield) are added whole."""
    merged = dict(base)
    quoted = 0
    disagreements = 0
    for pid, q in quotes.items():
        b = merged.get(pid)
        if b is None:
            merged[pid] = q
            log.info("product %s: reviews in Growave but no ssw.review metafield — using Growave aggregates", pid)
            continue
        if b.count != q.count or abs(b.avg - q.avg) > 0.10001:
            disagreements += 1
            log.debug(
                "product %s: metafield says %s/%d, growave says %s/%d",
                pid, b.avg, b.count, q.avg, q.count,
            )
        updates: dict[str, object] = {}
        for suffix in ("", "_lv", "_et"):
            if getattr(q, f"featured_text{suffix}"):
                for field in ("featured_text", "featured_author", "featured_rating"):
                    updates[field + suffix] = getattr(q, field + suffix)
        if updates:
            merged[pid] = b.model_copy(update=updates)
            quoted += 1
    log.info(
        "merge: %d products, %d with a featured quote, %d aggregate disagreements (metafield kept)",
        len(merged), quoted, disagreements,
    )
    return merged


def summaries_full(*, require_growave: bool) -> dict[str, ProductSummary]:
    """v1 = v0 metafield summaries + Growave quotes. If Growave fails and it is
    not required, degrade to the v0 stars-only feed instead of failing the build
    (CLAUDE.md §2: v0 must keep standing)."""
    base = summaries_from_shopify()
    try:
        quotes = summaries_from_growave()
    except Exception:
        if require_growave:
            raise
        log.exception("growave fetch failed — publishing stars-only (v0) feed this run")
        return base
    return merge_quotes(base, quotes)


def summaries_from_fixture(path: str) -> dict[str, ProductSummary]:
    """Offline path: a JSON file shaped like a list of GraphQL product nodes
    ({legacyResourceId, metafield:{value}}), exercising the real parser."""
    nodes = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, ProductSummary] = {}
    for node in nodes:
        summary = parse_review_metafield(node)
        if summary is not None:
            out[summary.product_id] = summary
    log.info("fixture: %d nodes, %d with review metafield", len(nodes), len(out))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the Rossi reviews web feed")
    ap.add_argument("--source", choices=["auto", "shopify", "full", "fixture"], default="auto")
    ap.add_argument("--fixture", help="path to a product-nodes JSON file (with --source fixture)")
    ap.add_argument("--out", help="output path (default: OUTPUT_PATH / docs/reviews.json)")
    ap.add_argument("--flat", action="store_true", help="emit the bare products dict (no wrapper)")
    ap.add_argument("--force", action="store_true", help="bypass the collapse guard")
    args = ap.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.source == "fixture":
        if not args.fixture:
            ap.error("--source fixture requires --fixture PATH")
        summaries = summaries_from_fixture(args.fixture)
    elif args.source == "shopify":
        summaries = summaries_from_shopify()
    elif args.source == "full":
        summaries = summaries_full(require_growave=True)
    else:  # auto: v1 when Growave creds are configured, else v0
        if settings.growave_api_key and settings.growave_api_secret:
            summaries = summaries_full(require_growave=False)
        else:
            summaries = summaries_from_shopify()

    try:
        publish(
            summaries,
            args.out or settings.output_path,
            wrapped=settings.feed_wrapped and not args.flat,
            guard_ratio=settings.collapse_guard_ratio,
            force=args.force,
        )
    except CollapseError as exc:
        log.error("COLLAPSE GUARD: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
