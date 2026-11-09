"""Unit tests for the deterministic aggregation helpers."""

import pytest


@pytest.fixture
def mod(direct_deploy, direct_vm):
    # Deploying once puts the SDK on sys.path and loads the module.
    direct_deploy("contracts/quorum_feed.py")
    import sys

    for name, m in sys.modules.items():
        if hasattr(m, "aggregate") and hasattr(m, "QuorumFeed"):
            return m
    raise RuntimeError("contract module not found")


def test_median_odd_even(mod):
    assert mod._median([3, 1, 2]) == 2
    assert mod._median([4, 1, 3, 2]) == 2  # (2 + 3) // 2


def test_aggregate_drops_outlier(mod):
    obs = [(0, 10000), (1, 10010), (2, 12000)]
    out = mod.aggregate(obs, quorum=2, max_spread_bps=100)
    assert out["mask"] == 0b011
    assert out["value"] == 10005
    assert out["spread_bps"] <= 100


def test_aggregate_quorum_after_filtering(mod):
    obs = [(0, 100), (1, 200), (2, 400)]
    with pytest.raises(Exception, match="sources agree"):
        mod.aggregate(obs, quorum=2, max_spread_bps=10)


def test_aggregate_not_enough_answers(mod):
    with pytest.raises(Exception, match="sources answered"):
        mod.aggregate([(0, 1)], quorum=2, max_spread_bps=100)


def test_to_scaled(mod):
    assert mod._to_scaled("67,250.123", 2) == 6725012
    assert mod._to_scaled(1.5, 0) == 2  # banker's rounding to integral
    assert mod._to_scaled("-3.25", 2) == -325
    assert mod._to_scaled("12%", 1) == 120
    assert mod._to_scaled("n/a", 2) is None
    assert mod._to_scaled(True, 2) is None
    assert mod._to_scaled("NaN", 2) is None


def test_walk_json(mod):
    doc = {"result": {"X": {"c": ["101.5", "1"]}}, "list": [{"p": 1}, {"p": 2}]}
    assert mod._walk_json(doc, "result.X.c.0") == "101.5"
    assert mod._walk_json(doc, "list.-1.p") == 2
    assert mod._walk_json(doc, "list.5.p") is None
    assert mod._walk_json(doc, "missing.key") is None


def test_digit_runs_grounding(mod):
    runs = mod._digits_runs("BTC is trading at $67,250.10 today, up 2.4%")
    assert "|67250|" in runs
    assert "|10|" in runs
    assert "|6725|" not in runs
    assert "|250|" not in runs


def test_tolerance(mod):
    assert mod.within_tolerance(10050, 10000, 50)
    assert not mod.within_tolerance(10051, 10000, 50)
    assert mod.within_tolerance(0, 0, 1)
