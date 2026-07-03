"""Pure transform logic — no I/O, fully unit-tested.

All rounding/formatting happens here in Python so the Klaviyo template stays dumb
(CLAUDE.md §5.1). v0 uses `summary_from_counts`; v1 adds the review-level path
(`build_summaries`) for featured quotes."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from .models import ProductSummary, Review

FILLED_STAR = "★"
EMPTY_STAR = "☆"
_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


def render_stars(avg: float, width: int = 5) -> str:
    """Fixed-width star string, avg rounded to the nearest whole star (half-up).

    4.8 -> '★★★★★', 4.2 -> '★★★★☆'. Printed verbatim by the template."""
    full = int(Decimal(str(avg)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    full = max(0, min(width, full))
    return FILLED_STAR * full + EMPTY_STAR * (width - full)


def summary_from_counts(product_id: str, avg: float, count: int) -> ProductSummary:
    """v0: build a stars-only summary straight from the ssw.review metafield numbers."""
    avg = round(float(avg), 1)
    return ProductSummary(
        product_id=str(product_id),
        avg=avg,
        count=int(count),
        stars=render_stars(avg),
    )


def average_rating(reviews: list[Review]) -> float:
    if not reviews:
        return 0.0
    return round(sum(r.rating for r in reviews) / len(reviews), 1)


def truncate_text(text: str, max_len: int) -> str:
    """Collapse whitespace and truncate to <= max_len chars on a word boundary.

    The ellipsis counts toward max_len; an over-long single word is hard-cut."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,.;:!?-—") + "…"


def _eligible(reviews: list[Review], *, allowed: set[int], min_len: int) -> list[Review]:
    return [
        r
        for r in reviews
        if r.published and r.rating in allowed and len((r.body or "").strip()) >= min_len
    ]


def _best(reviews: list[Review]) -> Review:
    """Highest rating, then most recent (CLAUDE.md §7)."""
    return max(reviews, key=lambda r: (r.rating, r.created_at or _MIN_DT))


def select_featured(reviews: list[Review], *, min_len: int) -> Review | None:
    """CLAUDE.md §7: 5★, published, body >= min_len, most recent; else best >= 4★;
    else None (stars only)."""
    primary = _eligible(reviews, allowed={5}, min_len=min_len)
    if primary:
        return _best(primary)
    fallback = _eligible(reviews, allowed={4, 5}, min_len=min_len)
    if fallback:
        return _best(fallback)
    return None


def _quote_pool(reviews: list[Review], language: str | None) -> list[Review]:
    """Language policy (CLAUDE.md §7, informed by §3): the featured quote must be
    `language` (LT). Untagged reviews count as store-default language and stay in;
    reviews explicitly tagged another language are excluded. No cross-language
    fallback — wrong-language quotes are worse than no quote."""
    if not language:
        return reviews
    return [
        r for r in reviews
        if r.language is None or r.language.lower() == language.lower()
    ]


def summarize(
    product_id: str,
    reviews: list[Review],
    *,
    language: str | None,
    min_len: int,
    max_len: int,
) -> ProductSummary:
    """v1: aggregate one product's approved reviews (CLAUDE.md §5.1).

    count/avg/stars cover ALL approved reviews (must match ssw.review / the live
    site); only the featured quote is language-filtered."""
    count = len(reviews)
    avg = average_rating(reviews)
    featured = select_featured(_quote_pool(reviews, language), min_len=min_len)

    return ProductSummary(
        product_id=product_id,
        avg=avg,
        count=count,
        stars=render_stars(avg),
        featured_text=truncate_text(featured.body, max_len) if featured else None,
        featured_author=featured.author if featured else None,
        featured_rating=featured.rating if featured else None,
    )


def build_summaries(
    reviews: list[Review],
    *,
    language: str | None = None,
    min_len: int = 40,
    max_len: int = 200,
) -> dict[str, ProductSummary]:
    """v1: group approved reviews by Shopify product id and summarize each product."""
    by_product: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        if not r.published or not r.product_id:
            continue
        by_product[str(r.product_id)].append(r)

    return {
        pid: summarize(pid, revs, language=language, min_len=min_len, max_len=max_len)
        for pid, revs in by_product.items()
        if revs
    }
