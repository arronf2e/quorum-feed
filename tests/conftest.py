import json
import sys

import pytest

CONTRACT = "contracts/quorum_feed.py"

COINBASE = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
KRAKEN = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
BITSTAMP = "https://www.bitstamp.net/api/v2/ticker/btcusd/"
CMC_PAGE = "https://coinmarketcap.com/currencies/bitcoin/"


def warp(vm, ts: str) -> None:
    """Move the transaction clock.

    The contract reads time from gl.message_raw["datetime"], which the direct
    runner only fills in at deploy time, so keep it in sync here.
    """
    vm.warp(ts)
    gl = sys.modules.get("genlayer.gl")
    if gl is not None and getattr(gl, "message_raw", None) is not None:
        gl.message_raw["datetime"] = ts


def btc_sources(with_page: bool = False) -> list:
    sources = [
        {"url": COINBASE, "kind": "json", "selector": "data.amount"},
        {"url": KRAKEN, "kind": "json", "selector": "result.XXBTZUSD.c.0"},
        {"url": BITSTAMP, "kind": "json", "selector": "last"},
    ]
    if with_page:
        sources.append(
            {"url": CMC_PAGE, "kind": "page", "selector": "current BTC price in USD"}
        )
    return sources


def mock_prices(vm, coinbase=None, kraken=None, bitstamp=None):
    """Register JSON API mocks. Passing None makes that source return HTTP 503."""

    def body(status, payload):
        return {"status": status, "body": json.dumps(payload)}

    vm.mock_web(
        r".*api\.coinbase\.com.*",
        body(200, {"data": {"amount": str(coinbase), "currency": "USD"}})
        if coinbase is not None
        else {"status": 503, "body": ""},
    )
    vm.mock_web(
        r".*api\.kraken\.com.*",
        body(200, {"error": [], "result": {"XXBTZUSD": {"c": [str(kraken), "0.01"]}}})
        if kraken is not None
        else {"status": 503, "body": ""},
    )
    vm.mock_web(
        r".*bitstamp\.net.*",
        body(200, {"last": str(bitstamp), "volume": "1200.5"})
        if bitstamp is not None
        else {"status": 503, "body": ""},
    )


@pytest.fixture
def feed(direct_vm, direct_deploy, direct_alice):
    direct_vm.check_pickling = True  # GenVM ships leader/validator closures via cloudpickle
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    warp(direct_vm, "2026-11-01T12:00:00Z")
    c.create_feed(
        "BTC-USD",
        "Bitcoin spot price in US dollars",
        2,      # decimals
        2,      # quorum
        150,    # max_spread_bps: sources further than 1.5% from the median are dropped
        50,     # tolerance_bps: validators accept within 0.5%
        1000,   # max_jump_bps: >10% moves need confirmation
        3600,   # heartbeat (seconds)
        btc_sources(),
    )
    return c
