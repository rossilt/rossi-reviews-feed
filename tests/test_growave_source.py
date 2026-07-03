import httpx
import pytest
import respx

from rossi_reviews.growave_source import GrowaveSource, map_raw_review

BASE = "https://api.growave.io/v2"


def raw(**kw):
    base = {
        "id": 1,
        "title": None,
        "body": "Labai geras produktas, oda tapo svelnesne per dvi savaites",
        "rate": 5,
        "images": [],
        "votes": 0,
        "isPublished": True,
        "isPinned": False,
        "isVerifiedBuyer": True,
        "createdAt": "2026-07-01T10:06:35.388Z",
        "customer": {"shopifyCustomerId": 1, "email": "PRIVATE@example.com", "phone": "+370600"},
        "product": {"id": 7186958844109, "handle": "some-product"},
        "reply": None,
        "customerDisplayName": "Vilma K.",
    }
    base.update(kw)
    return base


# --- mapping (live-confirmed DTO shape) --------------------------------------
def test_map_raw_review_fields():
    r = map_raw_review(raw())
    assert r.product_id == "7186958844109"
    assert r.rating == 5
    assert r.author == "Vilma K."
    assert r.published is True
    assert r.language is None                     # Growave has no language field
    assert r.created_at.year == 2026


def test_map_never_leaks_pii():
    r = map_raw_review(raw())
    dumped = r.model_dump()
    assert "PRIVATE@example.com" not in str(dumped)
    assert "+370600" not in str(dumped)


def test_map_skips_shop_reviews_without_product():
    assert map_raw_review(raw(product=None)) is None


# --- pagination + auth refresh ------------------------------------------------
def _token_response():
    return httpx.Response(200, json={
        "accessToken": "tok_1", "tokenType": "Bearer", "expiresAt": "2026-07-03T10:51:41Z",
    })


def _page(items, total):
    return httpx.Response(200, json={
        "totalCount": total, "currentOffset": 0, "perPage": len(items), "items": items,
    })


@respx.mock
def test_fetch_reviews_paginates_and_refreshes_token():
    auth_route = respx.post(f"{BASE}/oauth/getAccessToken").mock(return_value=_token_response())
    reviews_route = respx.get(f"{BASE}/reviews/getReviews").mock(side_effect=[
        _page([raw(), raw(id=2, product={"id": 111, "handle": "x"})], 3),
        httpx.Response(401),                       # token expired mid-run
        _page([raw(id=3, product=None)], 3),       # last item is a shop review -> skipped
    ])

    with GrowaveSource("key", "secret", page_size=2) as src:
        out = src.fetch_reviews()

    assert auth_route.call_count == 2              # initial + re-auth after 401
    assert reviews_route.call_count == 3
    assert [r.product_id for r in out] == ["7186958844109", "111"]


def test_source_requires_credentials():
    with pytest.raises(ValueError):
        GrowaveSource("", "")
