# Rossi — Product Reviews in Klaviyo Emails (v2)

Publishes each product's **rating, review count, and one featured 5★ quote** as a
JSON document that **Klaviyo** reads as a *web feed* — real social proof in the
Browse Abandonment flow and newsletters, **without paying for Klaviyo Reviews**.

Join key everywhere: the **Shopify product ID**. Full brief: [CLAUDE.md](CLAUDE.md).

## Staged build

| Stage | Source | Delivers | Status |
|---|---|---|---|
| **v0** | Shopify `ssw.review` metafield (Growave-maintained) via Admin GraphQL | `stars`, `avg`, `count` for all 3 markets (LT/LV/EE) | ✅ built; metafield shape live-verified 2026-07-03 |
| **v1** | Growave API | `featured_text` quote (LT emails only) | gated on Phase 0 T3 (`growave_source.py`) |

```
Shopify metafields ──▶ build (parse → stars → emit+guard) ──▶ docs/reviews.json
        (v0)                    GitHub Actions, daily              GitHub Pages
Growave API ────────▶ (v1 adds featured quotes)                        │
                                                                       ▼
                                     Klaviyo web feed ──▶ feeds.rossi_reviews.products|lookup:item.id
```

No server. A scheduled GitHub Actions workflow rebuilds the feed daily (~04:00
Vilnius), commits `docs/reviews.json`, and GitHub Pages serves it.

## Layout

| Path | Purpose |
|---|---|
| `rossi_reviews/transform.py` | stars, truncation, §7 featured-selection rules (pure, tested) |
| `rossi_reviews/shopify_source.py` | v0: paginated metafield read; handles the live-observed string/number value mix |
| `rossi_reviews/growave_source.py` | v1: Growave client — `TODO(T3)` markers to confirm against live docs |
| `rossi_reviews/emit.py` | §8 document, count>0 filter, `generated_at`, **collapse guard**, atomic write |
| `rossi_reviews/build.py` | CLI orchestrator (`--source shopify\|fixture`, `--flat`, `--force`) |
| `.github/workflows/build-feed.yml` | daily cron + manual `workflow_dispatch` |
| `docs/reviews.json` | the published feed (committed output — this IS the hosting) |

## Ops hardening (CLAUDE.md §5.1)

- **Atomic publish** — temp file + `os.replace`; never a half-written feed.
- **Collapse guard** — if products or total reviews drop below 50% of the
  previous run, the build **fails loudly** (exit 2), the old file stays
  published. Bypass deliberately with `--force` / the `force_rebuild` input.
- `generated_at` (Europe/Vilnius) at the root for staleness monitoring.
- Per-run logs: products scanned/emitted, unparseable metafields, skips.

## Local usage

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows paths
.venv/Scripts/python -m pytest

# offline build from the test fixture
.venv/Scripts/python -m rossi_reviews.build --source fixture \
    --fixture tests/fixtures/product_nodes.json --out docs/reviews.json

# real v0 build (needs .env — see .env.example)
.venv/Scripts/python -m rossi_reviews.build
```

## Deploy checklist

1. Push this repo to GitHub (public repo, or private on a plan with Pages).
2. Settings → **Secrets and variables → Actions**: add `SHOPIFY_SHOP` and
   `SHOPIFY_ADMIN_TOKEN` (custom app, `read_products` scope).
3. Settings → **Pages**: deploy from branch `main`, folder `/docs`.
4. Actions → *Build reviews feed* → **Run workflow** (first run).
5. Feed URL: `https://<user>.github.io/<repo>/reviews.json` → becomes the
   Klaviyo web feed `rossi_reviews`.

## Output schema (CLAUDE.md §8)

```json
{
  "generated_at": "2026-07-03T04:00:12+03:00",
  "products": {
    "6923022106829": {
      "product_id": "6923022106829",
      "avg": 4.8, "count": 108, "stars": "★★★★★",
      "featured_text": null, "featured_author": null, "featured_rating": null
    }
  }
}
```

Keys are strings; only `count > 0` products are included; v0 emits the
`featured_*` fields as `null`. If T2 shows the wrapper breaks Klaviyo's
`|lookup`, rebuild with `--flat` (or `FEED_WRAPPED=false`).

## Phase 0 status (CLAUDE.md §6) — T2 + T4 PASSED live (2026-07-03)

- **v0 source shape — done**: `ssw.review` metafield type `json`; values
  inconsistently number/string-typed (parser coerces both);
  `legacyResourceId` == embedded `product_id`; unreviewed products → `null`.
- **T2 — done**: feed `rossi_reviews` registered (Klaviyo feed 8744194), full
  1,166-product document ingested, status *Healthy*. The **wrapped root works**
  (`feeds.rossi_reviews.products|lookup:…` + `.generated_at` readable);
  `|lookup` works with literal AND variable keys; misses are clean falsy.
- **T4 — done, with a catch**: on `Kliento_perziuretos_prekes` items,
  **`item.id` is Klaviyo's internal integer id — the Shopify product id is
  `item.external_id` (string)**. String-to-string keying, no coercion issue.
  The join was verified 6/6 on live data in a hybrid-template preview.
- **Klaviyo deliverables live**: template `VxjG2P` ("Rossi — Peržiūrėtos prekės
  su atsiliepimais (LT)") + Universal Content block *"Perziuretos prekes su
  atsiliepimais (LT)"*. Block source: [klaviyo/review_block.html](klaviyo/review_block.html).
  Note: dynamic feeds only hydrate in D&D-template previews/sends (a minimized
  product block bound to the feed primes it), not in bare CODE-template previews.
- **T3 (Growave API)** — open; gates v1 (featured quote text) only.
