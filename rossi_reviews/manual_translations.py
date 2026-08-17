"""One-time manual LV/ET translations of LT featured quotes (2026-08 fill).

Native LV/ET quotes accrue too slowly (62 LV / 4 ET vs 866 LT at fill time), so
the LT featured quotes were exported, translated by hand once, and committed as
`translations/manual_quotes.json`. The build merges them in with the rule:
**native quote → manual translation → stars only.**

Translations are pinned by product id, NOT by the current LT featured pick — the
pick churns ("most recent 5★") and the one-time fill must not decay with it. A
pinned translation is still a real translated customer review; it retires
automatically the moment a native review in that language qualifies.

File shape (see scripts/import_translations.py, which generates it):

    {"generated_at": "...", "products": {
        "7128616763597": {"source_text": "Geras kremas...", "author": "Agnė",
                          "rating": 5, "lv": "Labs krēms...", "et": null}}}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import ProductSummary

log = logging.getLogger(__name__)

LANGS = ("lv", "et")


def load_manual_translations(path: str | Path) -> dict[str, dict]:
    """The products dict from the committed translations file; {} if the file
    does not exist (the pre-fill state). A present-but-broken file raises —
    a corrupt commit must fail the build, not silently strip every quote."""
    p = Path(path)
    if not p.exists():
        log.info("no manual translations at %s — skipping", p)
        return {}
    # utf-8-sig: also accept a BOM from Windows editors hand-touching the file
    doc = json.loads(p.read_text(encoding="utf-8-sig"))
    products = doc.get("products", {})
    log.info("loaded %d manual quote translations from %s", len(products), p)
    return products


def apply_manual_translations(
    summaries: dict[str, ProductSummary], translations: dict[str, dict]
) -> dict[str, ProductSummary]:
    """Fill empty featured_*_lv/_et slots from the manual translations.
    Products with a native quote in that language are left untouched."""
    if not translations:
        return summaries
    out = dict(summaries)
    applied = {lang: 0 for lang in LANGS}
    kept_native = 0
    for pid, t in translations.items():
        s = out.get(pid)
        if s is None:
            continue  # product no longer in the feed (delisted / reviews gone)
        updates: dict[str, object] = {}
        for lang in LANGS:
            text = (t.get(lang) or "").strip()
            if not text:
                continue
            if getattr(s, f"featured_text_{lang}"):
                kept_native += 1
                continue
            updates[f"featured_text_{lang}"] = text
            updates[f"featured_author_{lang}"] = t.get("author")
            updates[f"featured_rating_{lang}"] = t.get("rating")
            updates[f"featured_translated_{lang}"] = True
            applied[lang] += 1
        if updates:
            out[pid] = s.model_copy(update=updates)
    log.info(
        "manual translations: %d LV + %d ET applied, %d skipped (native quote exists)",
        applied["lv"], applied["et"], kept_native,
    )
    return out
