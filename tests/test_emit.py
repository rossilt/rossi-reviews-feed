import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from rossi_reviews.emit import (
    CollapseError,
    build_document,
    check_collapse,
    extract_products,
    load_previous,
    publish,
    write_atomic,
)
from rossi_reviews.transform import summary_from_counts


def summaries(*specs):
    """specs: (pid, avg, count)"""
    return {pid: summary_from_counts(pid, avg, count) for pid, avg, count in specs}


# --- build_document ----------------------------------------------------------
def test_document_wrapped_shape_and_zero_count_filter():
    now = datetime(2026, 7, 3, 4, 0, 12, tzinfo=ZoneInfo("Europe/Vilnius"))
    doc = build_document(summaries(("200", 4.8, 10), ("100", 0, 0)), now=now)
    assert doc["generated_at"] == "2026-07-03T04:00:12+03:00"
    assert set(doc["products"]) == {"200"}          # count==0 omitted (§4)
    rec = doc["products"]["200"]
    assert rec["product_id"] == "200" and rec["stars"] == "★★★★★"


def test_v2_language_keys_dropped_when_absent():
    plain = summary_from_counts("1", 4.8, 3)
    with_lv = summary_from_counts("2", 5.0, 2).model_copy(update={
        "featured_text_lv": "Ļoti labi mitrina ādu, iesaku visiem draugiem",
        "featured_author_lv": "Iveta P.", "featured_rating_lv": 5,
    })
    doc = build_document({"1": plain, "2": with_lv})
    assert "featured_text_lv" not in doc["products"]["1"]      # nulls dropped
    assert "featured_text_et" not in doc["products"]["2"]
    assert doc["products"]["1"]["featured_text"] is None       # §8 base keys stay
    assert doc["products"]["2"]["featured_text_lv"].startswith("Ļoti labi")
    assert doc["products"]["2"]["featured_rating_lv"] == 5


def test_document_flat_shape():
    doc = build_document(summaries(("100", 4.0, 2)), wrapped=False)
    assert set(doc) == {"100"}                      # no wrapper keys at all
    assert doc["100"]["avg"] == 4.0


def test_extract_products_handles_both_shapes():
    products = {"1": {"count": 2}}
    assert extract_products({"generated_at": "x", "products": products}) == products
    assert extract_products(products) == products
    assert extract_products(None) == {}


# --- collapse guard (§5.1) ---------------------------------------------------
def _prods(n, count_each=2):
    return {str(i): {"count": count_each} for i in range(n)}


def test_guard_passes_first_run_and_growth():
    check_collapse(_prods(10), {})                  # no previous file
    check_collapse(_prods(10), _prods(5))           # growth fine
    check_collapse(_prods(6), _prods(10))           # 60% of previous: above the 50% bar


def test_guard_trips_on_product_collapse():
    with pytest.raises(CollapseError, match="product count"):
        check_collapse(_prods(4), _prods(10))


def test_guard_trips_on_review_collapse():
    old = {"1": {"count": 100}, "2": {"count": 100}}
    new = {"1": {"count": 10}, "2": {"count": 10}}  # products stable, reviews cratered
    with pytest.raises(CollapseError, match="review count"):
        check_collapse(new, old)


# --- write / publish ---------------------------------------------------------
def test_write_atomic_utf8(tmp_path):
    out = tmp_path / "nested" / "reviews.json"
    write_atomic({"products": {"1": {"stars": "★★★★☆"}}}, out)
    text = out.read_text(encoding="utf-8")
    assert "★★★★☆" in text                          # not ascii-escaped
    assert json.loads(text)["products"]["1"]["stars"] == "★★★★☆"


def test_publish_guard_keeps_old_file(tmp_path):
    out = tmp_path / "reviews.json"
    publish(summaries(*[(str(i), 4.5, 5) for i in range(10)]), out)
    before = out.read_text(encoding="utf-8")

    with pytest.raises(CollapseError):
        publish(summaries(("1", 4.5, 5)), out)      # 10 -> 1 products: trip
    assert out.read_text(encoding="utf-8") == before  # old file intact

    doc = publish(summaries(("1", 4.5, 5)), out, force=True)  # explicit override
    assert set(doc["products"]) == {"1"}


def test_load_previous_tolerates_garbage(tmp_path):
    p = tmp_path / "reviews.json"
    p.write_text("{{{{", encoding="utf-8")
    assert load_previous(p) is None
