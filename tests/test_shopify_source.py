import httpx
import pytest
import respx

from rossi_reviews.shopify_source import ShopifySource, fetch_access_token, parse_review_metafield


@respx.mock
def test_fetch_access_token_client_credentials():
    route = respx.post("https://test-shop.myshopify.com/admin/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "shpat_fresh", "scope": "read_products", "expires_in": 86399}
        )
    )
    token = fetch_access_token("test-shop.myshopify.com", "cid", "csecret")
    assert token == "shpat_fresh"
    sent = route.calls[0].request
    assert b'"grant_type": "client_credentials"' in sent.content or b'"grant_type":"client_credentials"' in sent.content


def node(pid, value):
    return {"legacyResourceId": pid, "metafield": {"value": value} if value is not None else None}


# --- parse_review_metafield: the live-observed variants ----------------------
def test_parse_numeric_values():
    s = parse_review_metafield(node("6054761234637", '{"count":6,"avg":5,"product_id":6054761234637}'))
    assert (s.product_id, s.count, s.avg, s.stars) == ("6054761234637", 6, 5.0, "★★★★★")


def test_parse_string_values_live_gotcha():
    # Live-confirmed on rossi.lt: some products store numbers as strings.
    s = parse_review_metafield(node("6540413993165", '{"count":"32","avg":"5","product_id":6540413993165}'))
    assert (s.count, s.avg) == (32, 5.0)


def test_parse_missing_metafield():
    assert parse_review_metafield(node("6540414189773", None)) is None


def test_parse_garbage_value():
    assert parse_review_metafield(node("1", "not json at all")) is None
    assert parse_review_metafield(node("2", '{"avg":4.5}')) is None   # missing count


def test_parse_mismatched_embedded_id_uses_legacy_resource_id():
    s = parse_review_metafield(node("3", '{"count":3,"avg":4.7,"product_id":9999}'))
    assert s.product_id == "3"


def test_parse_implausible_values_skipped():
    assert parse_review_metafield(node("4", '{"count":-1,"avg":4}')) is None
    assert parse_review_metafield(node("5", '{"count":3,"avg":11}')) is None


# --- pagination + throttle retry ---------------------------------------------
GQL_URL = "https://test-shop.myshopify.com/admin/api/2025-07/graphql.json"


def _page(nodes, has_next, cursor=None):
    return {"data": {"products": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": nodes,
    }}}


@respx.mock
def test_fetch_summaries_paginates_and_retries_throttle():
    responses = [
        httpx.Response(200, json=_page([node("1", '{"count":2,"avg":4.0}')], True, "CUR1")),
        httpx.Response(200, json={"errors": [{"extensions": {"code": "THROTTLED"}}]}),
        httpx.Response(200, json=_page([node("2", '{"count":"7","avg":"4.9"}'), node("3", None)], False)),
    ]
    route = respx.post(GQL_URL).mock(side_effect=responses)

    with ShopifySource("test-shop.myshopify.com", "shpat_test") as src:
        out = src.fetch_summaries()

    assert route.call_count == 3
    assert set(out) == {"1", "2"}
    assert out["2"].count == 7 and out["2"].stars == "★★★★★"


def test_source_requires_credentials():
    with pytest.raises(ValueError):
        ShopifySource("", "")
