from datetime import datetime, timezone

from rossi_reviews.models import Review
from rossi_reviews.transform import (
    average_rating,
    build_summaries,
    render_stars,
    select_featured,
    summarize,
    summary_from_counts,
    truncate_text,
)


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def r(**kw):
    base = dict(product_id="100", rating=5, body="x" * 50, published=True)
    base.update(kw)
    return Review(**base)


# --- render_stars / summary_from_counts (v0) --------------------------------
def test_render_stars_rounds_half_up():
    assert render_stars(4.8) == "★★★★★"
    assert render_stars(4.2) == "★★★★☆"
    assert render_stars(4.5) == "★★★★★"
    assert render_stars(0) == "☆☆☆☆☆"


def test_render_stars_clamps_out_of_range():
    assert render_stars(9) == "★★★★★"
    assert render_stars(-1) == "☆☆☆☆☆"


def test_summary_from_counts_v0_shape():
    s = summary_from_counts("6054761234637", 4.86, 6)
    assert s.product_id == "6054761234637"
    assert s.avg == 4.9                      # rounded to 1dp
    assert s.stars == "★★★★★"
    assert s.count == 6
    assert s.featured_text is None and s.featured_author is None and s.featured_rating is None


# --- average / truncate ------------------------------------------------------
def test_average_rating_one_decimal():
    assert average_rating([r(rating=5), r(rating=4), r(rating=4)]) == 4.3


def test_truncate_short_passes_through_and_normalizes_whitespace():
    assert truncate_text("  hello   world ", 200) == "hello world"


def test_truncate_word_boundary():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa " * 5
    out = truncate_text(text, 50)
    assert len(out) <= 50
    assert out.endswith("…")
    assert text.startswith(out[:-1])
    assert not out[:-1].endswith(" ")


def test_truncate_long_single_word_hard_cut():
    out = truncate_text("x" * 300, 200)
    assert len(out) == 200
    assert out.endswith("…")


# --- select_featured (§7) ----------------------------------------------------
def test_featured_prefers_most_recent_five_star():
    revs = [
        r(body="great product, very smooth and gentle on the skin", created_at=_dt(2024, 1, 1), author="A"),
        r(body="excellent, my skin feels much softer after a few weeks", created_at=_dt(2024, 6, 1), author="B"),
    ]
    assert select_featured(revs, min_len=40).author == "B"


def test_featured_skips_short_five_star():
    revs = [
        r(body="Great!", created_at=_dt(2024, 6, 1)),
        r(body="this serum genuinely improved my skin texture a lot", created_at=_dt(2024, 1, 1), author="LONG"),
    ]
    assert select_featured(revs, min_len=40).author == "LONG"


def test_featured_falls_back_to_four_star():
    revs = [
        r(rating=3, body="this is an average product with a long enough body text", created_at=_dt(2024, 1, 1)),
        r(rating=4, body="pretty good moisturizer that absorbs quickly and well", created_at=_dt(2024, 2, 1), author="FOUR"),
    ]
    f = select_featured(revs, min_len=40)
    assert f.rating == 4 and f.author == "FOUR"


def test_featured_none_when_only_low_or_short():
    assert select_featured([r(rating=3, body="x" * 50), r(rating=5, body="ok")], min_len=40) is None


def test_featured_ignores_unpublished():
    revs = [r(body="a wonderfully detailed review of this fine product", published=False)]
    assert select_featured(revs, min_len=40) is None


# --- language policy (§7: LT-only quote, untagged = store default) ----------
def test_quote_excludes_other_language_no_fallback():
    revs = [
        r(body="great product, skin got noticeably softer within two weeks", language="en", author="EN"),
        r(rating=4, body="labai geras kremas, oda tapo zymiai svelnesne ir atrodo geriau", language="lt", author="LT4"),
    ]
    s = summarize("100", revs, language="lt", min_len=40, max_len=200)
    assert s.count == 2                    # counts cover ALL approved reviews
    assert s.featured_author == "LT4"      # 5★ EN excluded; falls to LT 4★, NOT to EN


def test_quote_treats_untagged_as_store_default():
    revs = [r(body="ilgas lietuviskas atsiliepimas be jokios kalbos zymos, bet tinkamas", language=None, author="X")]
    s = summarize("100", revs, language="lt", min_len=40, max_len=200)
    assert s.featured_author == "X"


def test_quote_none_when_only_foreign_language_qualifies():
    revs = [r(body="great product, skin got noticeably softer within two weeks", language="en")]
    s = summarize("100", revs, language="lt", min_len=40, max_len=200)
    assert s.featured_text is None and s.count == 1


# --- build_summaries ---------------------------------------------------------
def test_build_summaries_groups_and_drops_unpublished():
    revs = [
        r(product_id="100", body="a genuinely helpful and sufficiently long review body"),
        r(product_id="200", body="hidden review body that is plenty long here", published=False),
    ]
    out = build_summaries(revs, language="lt", min_len=40, max_len=200)
    assert set(out.keys()) == {"100"}
    assert out["100"].count == 1
