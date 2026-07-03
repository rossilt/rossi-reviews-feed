import json
from pathlib import Path

from rossi_reviews.build import main

FIX = str(Path(__file__).parent / "fixtures" / "product_nodes.json")


def test_cli_fixture_build_end_to_end(tmp_path):
    out = tmp_path / "reviews.json"
    assert main(["--source", "fixture", "--fixture", FIX, "--out", str(out)]) == 0

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "generated_at" in doc
    products = doc["products"]
    # 8 fixture nodes -> 5 emitted: null metafield, garbage json, and count==0 dropped
    assert set(products) == {
        "6054761234637", "6540411764941", "6540413993165", "6556857729229", "7000000000003",
    }
    assert products["6540413993165"]["count"] == 32          # string-typed source values
    assert products["6540413993165"]["stars"] == "★★★★★"
    assert all(p["featured_text"] is None for p in products.values())  # v0: stars only


def test_cli_collapse_guard_exit_code(tmp_path):
    out = tmp_path / "reviews.json"
    assert main(["--source", "fixture", "--fixture", FIX, "--out", str(out)]) == 0

    lone = tmp_path / "one_node.json"
    lone.write_text(
        json.dumps([{"legacyResourceId": "1", "metafield": {"value": '{"count":1,"avg":5}'}}]),
        encoding="utf-8",
    )
    assert main(["--source", "fixture", "--fixture", str(lone), "--out", str(out)]) == 2
    # guard kept the old file
    assert len(json.loads(out.read_text(encoding="utf-8"))["products"]) == 5
    # --force overrides
    assert main(["--source", "fixture", "--fixture", str(lone), "--out", str(out), "--force"]) == 0


def test_cli_flat_shape(tmp_path):
    out = tmp_path / "flat.json"
    assert main(["--source", "fixture", "--fixture", FIX, "--out", str(out), "--flat"]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "generated_at" not in doc and "6054761234637" in doc
