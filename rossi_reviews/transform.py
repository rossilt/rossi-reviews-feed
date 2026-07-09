"""Pure transform logic — no I/O, fully unit-tested.

All rounding/formatting happens here in Python so the Klaviyo template stays dumb
(CLAUDE.md §5.1). v0 uses `summary_from_counts`; v1 adds the review-level path
(`build_summaries`) for featured quotes; v2 buckets quotes by DETECTED text
language — `featured_text` stays store-default (LT), `featured_text_lv/_et`
serve the other markets (CLAUDE.md §7 v2 note)."""
from __future__ import annotations

import re
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


# --- review language detection (v2, CLAUDE.md §7) ----------------------------
# Growave exposes no language field, and "untagged = store-default LT" broke the
# moment localized review-request flows started producing LV/EE reviews: a live
# Latvian quote surfaced as featured_text and reached LT emails + the assistant.
# Dependency-free two-signal heuristic, tuned on the real corpus:
#   * letters unique to one of the three alphabets (weight 2 per occurrence) —
#     NB ū is LT *and* LV, š/ž/č are shared too; only the sets below are unique;
#   * distinctive common words (weight 1 each), lists pruned of every LT/LV/ET/EN
#     collision ("ir", "bet", "tik", "kas", "gan", "ja", "see", "man"...).
# English gets a stopword-only score so confident EN is excluded from every
# featured pool. Diacritic-less typing is common — hence the word signal.

FEATURED_LANGS = ("lt", "lv", "et")

_UNIQUE_CHARS = {
    "lt": "ąęėįųĄĘĖĮŲ",
    "lv": "āēīķļņģĀĒĪĶĻŅĢ",
    "et": "õäöüÕÄÖÜ",
}

_STOPWORDS = {
    "lt": frozenset(
        "yra labai mano oda odai odos veido plaukai plaukams plaukų plauku kvapas "
        "puikus puiki puikiai gerai greitai tinka patiko patinka rekomenduoju "
        "naudoju naudojimo tapo kaip nes kremas serumas priemonė priemone".split()
    ),
    "lv": frozenset(
        "un ļoti loti labi laba āda ādai ādu ada adai patīk patik iesaku lieliski "
        "seja sejas mati matiem krēms serums pērku perku var pēc pec šampūns".split()
    ),
    "et": frozenset(
        "on väga vaga hea nahk nahale naha juuksed juustele lõhn meeldib kasutan "
        "peale ning aga toode kreem seerum mulle".split()
    ),
    "en": frozenset(
        "the and is very this skin hair love great good product smell after with "
        "use using recommend for it my".split()
    ),
}

_WORD_RE = re.compile(r"[^\W\d_]+")


def detect_language(text: str) -> str | None:
    """'lt' | 'lv' | 'et' | 'en' from review body text; None = undetermined.

    A language wins with score >= 2 and a strict lead over the runner-up —
    otherwise None, and the caller applies the store-default rule."""
    if not text:
        return None
    words = [w.lower() for w in _WORD_RE.findall(text)]
    scores: dict[str, int] = {}
    for lang in ("lt", "lv", "et", "en"):
        chars = _UNIQUE_CHARS.get(lang, "")
        char_hits = sum(text.count(c) for c in chars)
        word_hits = sum(1 for w in words if w in _STOPWORDS[lang])
        scores[lang] = 2 * char_hits + word_hits
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, best_score), (_, second_score) = ranked[0], ranked[1]
    if best_score < 2 or best_score == second_score:
        return None
    return best


def _featured_bucket(review: Review, default: str) -> str | None:
    """Which featured pool a review may quote into. An explicit platform tag
    wins; else detect from the body; undetectable keeps the assumed-store-
    default rule; confidently other-language (e.g. EN) joins no pool at all —
    wrong-language quotes are worse than no quote."""
    tag = (review.language or "").strip().lower()[:2] or None
    lang = tag or detect_language(review.body)
    if lang is None:
        lang = default
    return lang if lang in FEATURED_LANGS else None


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


def summarize(
    product_id: str,
    reviews: list[Review],
    *,
    language: str | None,
    min_len: int,
    max_len: int,
) -> ProductSummary:
    """v1/v2: aggregate one product's approved reviews (CLAUDE.md §5.1).

    count/avg/stars cover ALL approved reviews (must match ssw.review / the live
    site); featured quotes are bucketed by language: the unsuffixed featured_*
    fields hold the store-default (`language`) quote, featured_*_lv/_et the
    other markets'. `language=None` keeps the legacy no-policy behaviour."""
    count = len(reviews)
    avg = average_rating(reviews)

    def _fields(pick: Review | None, suffix: str = "") -> dict:
        return {
            f"featured_text{suffix}": truncate_text(pick.body, max_len) if pick else None,
            f"featured_author{suffix}": pick.author if pick else None,
            f"featured_rating{suffix}": pick.rating if pick else None,
        }

    if not language:
        featured = _fields(select_featured(reviews, min_len=min_len))
    else:
        default = language.lower()
        pools: dict[str, list[Review]] = {lang: [] for lang in FEATURED_LANGS}
        pools.setdefault(default, [])
        for review in reviews:
            bucket = _featured_bucket(review, default)
            if bucket in pools:
                pools[bucket].append(review)
        featured = _fields(select_featured(pools[default], min_len=min_len))
        for lang in FEATURED_LANGS:
            if lang != default:
                featured.update(
                    _fields(select_featured(pools[lang], min_len=min_len), f"_{lang}")
                )

    return ProductSummary(
        product_id=product_id,
        avg=avg,
        count=count,
        stars=render_stars(avg),
        **featured,
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
