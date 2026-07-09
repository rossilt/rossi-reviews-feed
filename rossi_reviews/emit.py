"""Feed document assembly + ops hardening (CLAUDE.md §5.1 / §8):

- only count > 0 products are emitted (lookup miss == count 0 in the template);
- root wrapper with `generated_at` (Europe/Vilnius) — switchable to a bare dict
  if T2 shows the wrapper breaks Klaviyo's `|lookup`;
- collapse guard: if products or total reviews drop >ratio vs the previous
  published file, raise instead of publishing (silent auth breakage must not
  feed the emails an empty feed);
- atomic write: temp file in the target dir, then os.replace.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import ProductSummary

log = logging.getLogger(__name__)

VILNIUS = ZoneInfo("Europe/Vilnius")


class CollapseError(RuntimeError):
    """The new feed shrank suspiciously vs the published one; publish refused."""


def _sparse_dump(s: ProductSummary) -> dict:
    """§8 keeps the base featured_* keys (null when absent) for template
    stability; the v2 per-language keys are emitted only when present — a
    missing key is falsy to Django/Liquid lookups exactly like null."""
    return {
        k: v
        for k, v in s.model_dump().items()
        if v is not None or not k.endswith(("_lv", "_et"))
    }


def build_document(
    summaries: dict[str, ProductSummary],
    *,
    wrapped: bool = True,
    now: datetime | None = None,
) -> dict:
    """Assemble the §8 document from per-product summaries (count>0 only)."""
    products = {
        pid: _sparse_dump(s) for pid, s in sorted(summaries.items()) if s.count > 0
    }
    if not wrapped:
        return products
    now = now or datetime.now(VILNIUS)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "products": products,
    }


def extract_products(doc: dict | None) -> dict[str, dict]:
    """Products dict from either document shape (wrapped or bare)."""
    if not doc:
        return {}
    if "products" in doc and isinstance(doc["products"], dict):
        return doc["products"]
    return {k: v for k, v in doc.items() if isinstance(v, dict)}


def _total_reviews(products: dict[str, dict]) -> int:
    return sum(int(p.get("count") or 0) for p in products.values())


def check_collapse(new: dict[str, dict], old: dict[str, dict], *, ratio: float = 0.5) -> None:
    """Raise CollapseError if the new feed lost >(1-ratio) of products or reviews."""
    if not old:
        return
    if len(new) < len(old) * ratio:
        raise CollapseError(
            f"product count collapsed: {len(old)} -> {len(new)} "
            f"(guard at {ratio:.0%} of previous). Old file kept. "
            f"Check source auth/credentials; rerun with --force if the drop is real."
        )
    old_reviews, new_reviews = _total_reviews(old), _total_reviews(new)
    if new_reviews < old_reviews * ratio:
        raise CollapseError(
            f"total review count collapsed: {old_reviews} -> {new_reviews} "
            f"(guard at {ratio:.0%} of previous). Old file kept. "
            f"Check source auth/credentials; rerun with --force if the drop is real."
        )


def load_previous(path: str | Path) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("previous feed at %s unreadable (%s) — treating as first run", p, exc)
        return None


def write_atomic(doc: dict, path: str | Path) -> int:
    """Write compact UTF-8 JSON via temp file + os.replace; returns bytes written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return p.stat().st_size


def publish(
    summaries: dict[str, ProductSummary],
    path: str | Path,
    *,
    wrapped: bool = True,
    guard_ratio: float = 0.5,
    force: bool = False,
) -> dict:
    """Guarded publish: build -> collapse-check vs the file at `path` -> atomic write.
    Returns the written document."""
    doc = build_document(summaries, wrapped=wrapped)
    new_products = extract_products(doc)
    old_products = extract_products(load_previous(path))

    if force:
        if old_products:
            log.warning("--force: collapse guard bypassed")
    else:
        check_collapse(new_products, old_products, ratio=guard_ratio)

    size = write_atomic(doc, path)
    log.info(
        "published %d products (%d reviews) -> %s (%d bytes)",
        len(new_products), _total_reviews(new_products), path, size,
    )
    return doc
