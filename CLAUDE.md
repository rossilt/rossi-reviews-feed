# CLAUDE.md — Rossi: Product Reviews in Klaviyo Emails (v2)

> Self-contained brief for building this in Claude Code. The Claude Code session
> cannot see the conversation this came from — everything needed is here.
> Read §1, §2 and §6 before writing any code. Build **v0 first** (§2).

---

## 0. Goal

Build an automated tool that makes each product's **rating, review count, and one
featured 5★ review (text)** available inside **Klaviyo** emails — so the
recently-viewed-products block in the Browse Abandonment flow (and reusable blocks
in newsletters) can show real social proof per product, **without paying for
Klaviyo Reviews**.

- Store: rossi.lt (Korean skincare, Shopify). ~1,500 products.
- Markets: LT (primary), LV, EE — all three now receive localized flows.
- Review platform: **Growave** (formerly SocialShopWave → `ssw` namespace).
- Consumers: Browse Abandonment email 2 (social proof) + newsletter blocks.
- Refresh: daily. Real-time is NOT needed.

---

## 1. The hard constraint that shapes the whole design

**You CANNOT write custom data onto the Shopify-synced Klaviyo catalog via API.**
Klaviyo's Catalog API only operates on catalogs *created via API* (`$custom`),
never on `$shopify` integration catalogs. Shopify catalog items are read-only to
the API; no custom properties can be added to them.

➡️ Reviews must reach the email through a **side channel the template looks up by
Shopify product ID** — never by writing onto the existing product catalog.

The **join key across every system is the Shopify product ID** (e.g. `6923022106829`):
- Growave review payloads include it.
- Klaviyo's recently-viewed feed items are keyed on it (verify exact form — §6 T4).
- Growave's `ssw.review` Shopify metafield stores it.

---

## 2. Staged build — v0 ships without touching the Growave API

The one genuinely risky unknown is the Growave API (§9). Stars and counts do NOT
need it:

**v0 — stars + counts from Shopify metafields (build this first, zero unknowns):**
- Growave automatically maintains a Shopify product metafield
  **namespace `ssw`, key `review`** = `{"count":108,"avg":4.8,"product_id":...}`.
- Pipeline: Shopify Admin GraphQL (bulk operation over ~1,500 products, reading
  `metafield(namespace:"ssw", key:"review")`) → parse → emit the JSON feed (§8)
  with `stars/avg/count` only (`featured_text: null`).
- Value delivered immediately: star ratings + counts in emails for **all three
  markets** (numbers are language-neutral).

**v1 — featured review text via the Growave API (gated on Phase 0 §6):**
- Adds fetch → filter → group → select → truncate for the featured quote.
- Same emit step; the schema already has the fields.

If the Growave API proves painful, v0 still stands in production.

---

## 3. Confirmed facts (do not re-investigate — settled, several live-tested)

- **HTML blocks in this Klaviyo account render Django template code correctly** —
  live-verified in production emails: `{% for %}` loops over event arrays, and the
  `split`, `capfirst`, `cut` filters all work. Custom HTML lives in a dedicated
  **HTML block** or a **hybrid email** (the in-text-block source view is gone).
  Product feeds + custom HTML together = use a hybrid email.
- **Universal Content Blocks** are in active use in the account (header/footer);
  the finished review block should be saved as one → reuse everywhere.
- **Native star display is paywalled** (Klaviyo Reviews add-on). We render stars
  ourselves from our own data — that bypasses the paywall legitimately.
- **`ssw.review` metafield** holds `{avg, count, product_id}` per product,
  auto-maintained by Growave. It has NO review text — text only via Growave API.
- **Existing Klaviyo feed:** native product feed `Kliento_perziuretos_prekes`
  (recently viewed). Items expose `title`, `price`, `regular_price`, `url`,
  `image_full_url`. Reused as-is; reviews are looked up beside each item.
- **Growave does NOT honor customer locale in its payloads** (proven: an Estonian
  customer's event carried Lithuanian product titles despite EE translations
  existing in Shopify). Assume review data is store-default-language unless the
  API says otherwise → drives the language policy in §7.

---

## 4. Side channel — Option B primary, Option A fallback

### Option B — JSON Web Feed keyed by product ID  ★ v0 + v1 target
- One JSON document: dict keyed by Shopify product ID → review summary (§8).
- **Emit ONLY products with `count > 0`.** A lookup miss renders nothing — same
  as count==0 — and this slashes file size (many products have zero reviews;
  confirmed in real payloads). Size risk mostly evaporates.
- Host at a stable HTTPS URL; regenerate daily (§7 hosting default).
- Klaviyo: Account → Web feeds → add feed `rossi_reviews` → the URL.
- Template: `feeds.rossi_reviews|lookup:item.id` per viewed item.

### Option A — custom `$custom` catalog via Catalog API (fallback only)
- Upsert a second, API-created catalog; one item per product; review data in
  `custom_metadata`; template uses cross-catalog `{% catalog %}` (Appendix A).
- Use only if Option B's lookup or size fails Phase 0. More build surface:
  `$custom:::$default:::<id>` format, 409 upsert handling, rate limits, write scope.

---

## 5. Build components

### 5.1 The sync tool (THIS is the Claude Code project)
**v0:** Shopify bulk read of `ssw.review` metafields → parse → emit feed.
**v1 adds:** 1) fetch all Growave reviews (paginate) · 2) filter published/approved
· 3) group by product ID · 4) aggregate count/avg (sanity-check once against
`ssw.review` — must match the live site) · 5) select featured review (§7 rules)
· 6) render `stars` string in Python (template stays dumb) · 7) truncate text at
word boundary + `…` · 8) emit · 9) schedule daily.

**Ops hardening (both versions):**
- **Atomic publish:** write temp file, then swap — never serve a half-written feed.
- Root-level `generated_at` timestamp for staleness monitoring.
- **Collapse guard:** if product count or total reviews drop >50% vs the previous
  run, FAIL LOUDLY and keep the old file (protects against silent auth breakage
  feeding the emails an empty feed).
- Log per-run: products emitted, reviews processed, products skipped and why.

### 5.2 Klaviyo display (Klaviyo UI, not Claude Code)
- Hybrid email; "prime" the feed with a hidden product block pointed at
  `Kliento_perziuretos_prekes`; paste Appendix A; save as Universal Content.

---

## 6. PHASE 0 — validate before building (v0 needs only T2 + T4)

- [ ] **T2 — web feed lookup + size:** host a tiny test JSON (2–3 products) →
  add as Klaviyo web feed → confirm `feeds.<name>|lookup:item.id` resolves in a
  test template. **Key-type gotcha:** JSON keys are strings; check whether
  `item.id` renders as `1234` or `"1234"` and key the JSON to match exactly.
  Then confirm a full-size (all count>0 products) document loads untruncated.
- [ ] **T4 — feed item ID:** confirm what `item.id` resolves to on
  `Kliento_perziuretos_prekes` (expected: Shopify product ID). If different,
  THAT is the join key.
- [ ] **T3 — Growave API (gates v1 only):** with API key+secret, open the live
  interactive docs (https://api.growave.io/v2/docs) and confirm: list-reviews
  endpoint, response includes **body text**, **rating**, **Shopify product ID**,
  a **published/approved** flag, any **language** field, pagination, rate limits.
- [ ] **T1 (optional, already documented):** a Catalog API write to a `$shopify`
  item is rejected. Skip unless you want the proof in the repo README.

---

## 7. Decisions (defaults baked in)

- **Featured review selection:** rating == 5 → published only → body ≥ 40 chars →
  most recent; fallback to best ≥ 4★; else `featured_text: null` (stars only).
- **Featured text cap:** 200 chars, word-boundary truncate + `…`.
- **Language policy (decided — informed by §3 locale finding):**
  - Stars + count: **all three markets** (language-neutral).
  - Featured quote: **LT emails only in v1.** Do NOT put LT quotes into LV/EE
    emails (wrong-language experience the store has been systematically removing).
  - v2 (SHIPPED 2026-07-09): review text is language-detected (dependency-free
    char+stopword heuristic in transform.py — Growave payloads carry NO language
    field, and untagged LV reviews were leaking into `featured_text`); featured
    quotes are bucketed per language: `featured_text` is guaranteed LT,
    `featured_text_lv`/`_et` (+author/rating) serve the other markets. Confident
    non-LT/LV/ET (e.g. English) is excluded from every featured slot.
  - Template-side: the LV/EE blocks simply omit the quote markup; the schema is
    shared.
- **Refresh:** daily, ~04:00 Europe/Vilnius.
- **Hosting default (decided):** a GitHub repo with a **GitHub Actions scheduled
  workflow** (cron) running the script; output JSON published to **GitHub Pages**
  (or Cloudflare R2 if preferred). No server. Growave/Shopify secrets in
  **Actions secrets**, never in the repo.
- Klaviyo caches web feeds on its own cadence — fine for daily data.

---

## 8. Output data schema

```json
{
  "generated_at": "2026-07-03T04:00:12+03:00",
  "products": {
    "6923022106829": {
      "product_id": "6923022106829",
      "avg": 4.8,
      "count": 108,
      "stars": "★★★★★",
      "featured_text": "Oda po dviejų savaičių tapo daug švelnesnė...",
      "featured_author": "Greta",
      "featured_rating": 5
    }
  }
}
```
- Keys are **strings**, matching the exact form Klaviyo's `item.id` renders (T2).
- Only `count > 0` products included. v0 emits `featured_* : null`.
- If the root wrapper breaks `|lookup` in testing, flatten to the bare dict —
  verify in T2 which shape the lookup filter accepts.
- v2: `featured_text_lv`/`featured_author_lv`/`featured_rating_lv` (and `_et`)
  are emitted ONLY when a quote exists in that language — null per-language keys
  are dropped for size; a missing key is falsy in Django/Liquid lookups.

---

## 9. API reference

### Shopify Admin GraphQL (v0 source)
- Bulk operation (or paginated `products` query) selecting
  `metafield(namespace: "ssw", key: "review") { value }` per product; parse the
  JSON string value. Custom-app token with `read_products` scope.

### Growave (v1 source) — confirm specifics in Phase 0 T3
- Interactive docs: `https://api.growave.io/v2/docs` (JS-rendered; needs a browser).
- Auth: API key + secret + store URL (Growave admin → API keys). Confirm scheme live.
- Reviews carry: rating, title, body, product association, link, customer email,
  per-product average. TBD: exact paths, pagination, approved-flag and
  product-ID field names, any language field, rate limits.

### Klaviyo Catalog API (Option A fallback only)
- Create/Update Catalog Item; ID format `$custom:::$default:::<EXTERNAL_ID>`
  (Shopify product ID as external ID; note the triple colons).
- `custom_metadata` flat JSON ≤100kb/item. 409-on-create → GET then PATCH.
- Limits: burst 75/s, steady 750/min. Scope `catalogs:write`.
- Confirmed: cannot touch `$shopify` items (§1).

### Klaviyo web feeds (Option B)
- Account → Web feeds → add feed (name, no spaces) + HTTPS URL.
- Template: `feeds.<name>` ; per-item `feeds.<name>|lookup:item.id`.

---

## 10. Out of scope / non-goals
- Aggregate stars + one featured quote per product — NOT a review wall, NOT
  moderation tooling.
- The Klaviyo block is built in the Klaviyo UI (Appendix A), not in code.
- Never write to the Shopify-synced catalog (§1).
- Optional future idea (do not build now): also write the featured quote to a
  Shopify metafield for on-site theme use.

---

## Appendix A — Klaviyo block snippet (Option B; Django/Liquid, current design system)

Design tokens in use across all Rossi emails: Montserrat/Arial; navy `#0F2835`;
body `#313C42`; muted `#516971`; gold stars `#E8B500`; white cards `#FFFFFF`
with 1px `#E7EBED` border, 13px radius; cream surface `#FCF7F3`.

Per-item snippet (inside the repeating product cell, after title/price):

```django
{% with rev=feeds.rossi_reviews|lookup:item.id %}
  {% if rev and rev.count > 0 %}
    <div style="text-align:center; font-family:Montserrat,Arial,sans-serif; font-size:13px; line-height:1.3; padding-top:6px;">
      <span style="color:#E8B500; letter-spacing:1px;">{{ rev.stars }}</span>
      <span style="color:#516971;"> {{ rev.avg }} ({{ rev.count }})</span>
    </div>
    {% if rev.featured_text %}{# LT blocks only — omit this inner if in LV/EE blocks #}
      <p style="text-align:center; font-family:Montserrat,Arial,sans-serif; font-size:12px; font-style:italic; color:#313C42; margin:6px 12px 0; line-height:1.5;">
        &bdquo;{{ rev.featured_text }}&ldquo;
        {% if rev.featured_author %}
          <br><span style="font-style:normal; color:#516971;">&mdash; {{ rev.featured_author }}</span>
        {% endif %}
      </p>
    {% endif %}
  {% endif %}
{% endwith %}
```

Option A variant — swap the lookup line:
```django
{% catalog item.id integration="api" catalog_id="<REVIEWS_CATALOG_ID>" %}
  {% if catalog_item.metadata.count > 0 %} ... {% endif %}
{% endcatalog %}
```

Verify `|lookup` (and the schema shape it accepts) in Phase 0 T2 — that is the
single most likely thing to need a tweak.

## Appendix B — context links
- Klaviyo Catalogs API + Update Catalog Item (developers.klaviyo.com)
- Klaviyo `{% catalog %}` lookup tag reference (help.klaviyo.com)
- Klaviyo web feeds documentation (help.klaviyo.com)
- Growave developer toolkit (growave.io/developers-toolkit) + api.growave.io
