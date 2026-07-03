"""Build + publish the reviews feed (CLAUDE.md §5.1).

    python -m rossi_reviews.build                      # v0: Shopify metafields (default)
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
from .models import ProductSummary
from .shopify_source import ShopifySource, parse_review_metafield

log = logging.getLogger(__name__)


def summaries_from_shopify() -> dict[str, ProductSummary]:
    s = get_settings()
    with ShopifySource(s.shopify_shop, s.shopify_admin_token, s.shopify_api_version) as src:
        return src.fetch_summaries()


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
    ap.add_argument("--source", choices=["shopify", "fixture"], default="shopify")
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
