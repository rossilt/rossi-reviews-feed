from rossi_reviews.build import merge_quotes
from rossi_reviews.models import ProductSummary
from rossi_reviews.transform import summary_from_counts


def q(pid, avg, count, text=None, author=None, rating=None):
    s = summary_from_counts(pid, avg, count)
    return s.model_copy(update={
        "featured_text": text, "featured_author": author, "featured_rating": rating,
    })


def test_merge_attaches_quote_keeps_metafield_numbers():
    base = {"100": summary_from_counts("100", 4.8, 172)}
    quotes = {"100": q("100", 4.7, 170, text="Puikus kremas, tikrai rekomenduoju visiems", author="Greta", rating=5)}
    merged = merge_quotes(base, quotes)

    m = merged["100"]
    assert (m.avg, m.count, m.stars) == (4.8, 172, "★★★★★")   # metafield authoritative
    assert m.featured_text == "Puikus kremas, tikrai rekomenduoju visiems"
    assert m.featured_author == "Greta"
    assert m.featured_rating == 5


def test_merge_leaves_products_without_quotes_untouched():
    base = {"100": summary_from_counts("100", 4.8, 10)}
    merged = merge_quotes(base, {"100": q("100", 4.8, 10)})   # no featured text qualified
    assert merged["100"].featured_text is None
    assert merged["100"].count == 10


def test_merge_adds_growave_only_products():
    base = {"100": summary_from_counts("100", 4.8, 10)}
    quotes = {"200": q("200", 5.0, 2, text="Nuostabi priemone, naudoju kasdien jau menesi", author="R.")}
    merged = merge_quotes(base, quotes)
    assert set(merged) == {"100", "200"}
    assert merged["200"].featured_author == "R."


def test_merge_carries_per_language_quotes():
    base = {"100": summary_from_counts("100", 4.8, 20)}
    quotes = {"100": summary_from_counts("100", 4.8, 20).model_copy(update={
        "featured_text": "Puikus kremas, tikrai rekomenduoju visiems draugams",
        "featured_author": "Greta", "featured_rating": 5,
        "featured_text_lv": "Ļoti labi mitrina ādu, iesaku visiem draugiem",
        "featured_author_lv": "Iveta P.", "featured_rating_lv": 5,
    })}
    m = merge_quotes(base, quotes)["100"]
    assert m.featured_author == "Greta"
    assert m.featured_author_lv == "Iveta P."
    assert m.featured_text_et is None


def test_merge_attaches_lv_only_quote():
    base = {"100": summary_from_counts("100", 4.8, 20)}
    quotes = {"100": summary_from_counts("100", 4.8, 20).model_copy(update={
        "featured_text_lv": "Ļoti labi mitrina ādu, iesaku visiem draugiem",
        "featured_author_lv": "Iveta P.", "featured_rating_lv": 5,
    })}
    m = merge_quotes(base, quotes)["100"]
    assert m.featured_text is None                 # no LT quote invented
    assert m.featured_author_lv == "Iveta P."


def test_merge_result_is_all_product_summaries():
    base = {"100": summary_from_counts("100", 4.8, 10)}
    merged = merge_quotes(base, {})
    assert all(isinstance(v, ProductSummary) for v in merged.values())
