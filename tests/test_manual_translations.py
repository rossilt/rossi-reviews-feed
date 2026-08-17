import json

from rossi_reviews.emit import build_document
from rossi_reviews.manual_translations import (
    apply_manual_translations,
    load_manual_translations,
)
from rossi_reviews.transform import summary_from_counts


def t(lv=None, et=None, author="Agnė", rating=5):
    entry = {"source_text": "Geras kremas nuo saulės, nepalieka balto atspalvio",
             "author": author, "rating": rating}
    if lv:
        entry["lv"] = lv
    if et:
        entry["et"] = et
    return entry


def test_translation_fills_empty_lv_slot_with_flag():
    summaries = {"100": summary_from_counts("100", 4.8, 42)}
    out = apply_manual_translations(
        summaries, {"100": t(lv="Labs saules krēms, neatstāj baltu nokrāsu")}
    )
    s = out["100"]
    assert s.featured_text_lv == "Labs saules krēms, neatstāj baltu nokrāsu"
    assert s.featured_author_lv == "Agnė"
    assert s.featured_rating_lv == 5
    assert s.featured_translated_lv is True
    assert s.featured_text_et is None
    assert s.featured_translated_et is None


def test_native_quote_wins_over_translation():
    native = summary_from_counts("100", 4.8, 42).model_copy(update={
        "featured_text_lv": "Ļoti labi mitrina ādu, iesaku visiem draugiem",
        "featured_author_lv": "Iveta P.", "featured_rating_lv": 5,
    })
    out = apply_manual_translations(
        {"100": native}, {"100": t(lv="Labs saules krēms, neatstāj baltu nokrāsu")}
    )
    s = out["100"]
    assert s.featured_author_lv == "Iveta P."          # native untouched
    assert s.featured_translated_lv is None            # and not marked translated


def test_native_lv_does_not_block_et_translation():
    native_lv = summary_from_counts("100", 4.8, 42).model_copy(update={
        "featured_text_lv": "Ļoti labi mitrina ādu, iesaku visiem draugiem",
    })
    out = apply_manual_translations(
        {"100": native_lv},
        {"100": t(lv="Labs krēms", et="Hea päikesekreem, ei jäta valget tooni")},
    )
    s = out["100"]
    assert s.featured_translated_lv is None
    assert s.featured_text_et == "Hea päikesekreem, ei jäta valget tooni"
    assert s.featured_translated_et is True


def test_translation_for_missing_product_is_ignored():
    summaries = {"100": summary_from_counts("100", 4.8, 42)}
    out = apply_manual_translations(summaries, {"999": t(lv="Labs krēms")})
    assert out == summaries


def test_empty_translations_are_a_noop():
    summaries = {"100": summary_from_counts("100", 4.8, 42)}
    assert apply_manual_translations(summaries, {}) is summaries


def test_load_missing_file_returns_empty(tmp_path):
    assert load_manual_translations(tmp_path / "nope.json") == {}


def test_load_reads_products_dict(tmp_path):
    p = tmp_path / "manual_quotes.json"
    p.write_text(json.dumps({"generated_at": "x", "products": {"100": t(lv="Labs")}}),
                 encoding="utf-8")
    loaded = load_manual_translations(p)
    assert loaded["100"]["lv"] == "Labs"


def test_emit_keeps_flag_when_true_drops_when_none():
    summaries = {"100": summary_from_counts("100", 4.8, 42)}
    out = apply_manual_translations(
        summaries, {"100": t(lv="Labs saules krēms, neatstāj baltu nokrāsu")}
    )
    product = build_document(out, wrapped=False)["100"]
    assert product["featured_translated_lv"] is True
    assert "featured_translated_et" not in product     # None per-language keys dropped
    assert "featured_text_et" not in product
