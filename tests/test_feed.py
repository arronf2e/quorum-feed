from tests.conftest import CONTRACT, btc_sources, mock_prices, warp


def test_create_feed_validation(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)

    with direct_vm.expect_revert("quorum must be"):
        c.create_feed("X", "x", 2, 4, 100, 50, 0, 60, btc_sources())

    with direct_vm.expect_revert("https"):
        c.create_feed(
            "X", "x", 2, 1, 100, 50, 0, 60,
            [{"url": "http://example.com", "kind": "json", "selector": "a"}],
        )

    with direct_vm.expect_revert("selector hint"):
        c.create_feed(
            "X", "x", 2, 1, 100, 50, 0, 60,
            [{"url": "https://example.com", "kind": "page", "selector": ""}],
        )

    with direct_vm.expect_revert("duplicate"):
        dup = {"url": "https://example.com/a", "kind": "json", "selector": "a"}
        c.create_feed("X", "x", 2, 1, 100, 50, 0, 60, [dup, dup])

    c.create_feed("X", "x", 2, 2, 100, 50, 0, 60, btc_sources())
    with direct_vm.expect_revert("already exists"):
        c.create_feed("X", "x", 2, 2, 100, 50, 0, 60, btc_sources())

    assert c.list_feeds() == ["X"]


def test_fresh_feed_is_stale(feed, direct_vm):
    info = feed.latest("BTC-USD")
    assert info["round_id"] == 0
    assert info["stale"] is True
    with direct_vm.expect_revert("stale"):
        feed.read_fresh("BTC-USD")


def test_update_commits_median(feed, direct_vm):
    mock_prices(direct_vm, coinbase="67250.10", kraken="67262.40", bitstamp="67240.00")
    out = feed.update("BTC-USD")
    assert out["status"] == "committed"

    info = feed.latest("BTC-USD")
    assert info["round_id"] == 1
    assert info["value"] == 6725010
    assert info["sources_used"] == 3
    assert info["stale"] is False
    assert feed.read_fresh("BTC-USD") == 6725010


def test_outlier_source_is_ignored(feed, direct_vm):
    mock_prices(direct_vm, coinbase="67250.00", kraken="67260.00", bitstamp="71000.00")
    feed.update("BTC-USD")
    info = feed.latest("BTC-USD")
    assert info["sources_used"] == 2
    assert info["value"] == 6725500


def test_one_source_down_still_reaches_quorum(feed, direct_vm):
    mock_prices(direct_vm, coinbase="67250.00", kraken=None, bitstamp="67270.00")
    feed.update("BTC-USD")
    assert feed.latest("BTC-USD")["value"] == 6726000


def test_quorum_failure_reverts(feed, direct_vm):
    mock_prices(direct_vm, coinbase="67250.00", kraken=None, bitstamp=None)
    with direct_vm.expect_revert("quorum not reached"):
        feed.update("BTC-USD")
    assert feed.latest("BTC-USD")["round_id"] == 0


def test_heartbeat_staleness(feed, direct_vm):
    mock_prices(direct_vm, coinbase="100", kraken="100", bitstamp="100")
    feed.update("BTC-USD")
    warp(direct_vm, "2026-11-01T12:59:00Z")
    assert feed.latest("BTC-USD")["stale"] is False
    warp(direct_vm, "2026-11-01T13:00:01Z")
    assert feed.latest("BTC-USD")["stale"] is True
    with direct_vm.expect_revert("stale"):
        feed.read_fresh("BTC-USD")


def test_circuit_breaker_holds_then_confirms(feed, direct_vm):
    mock_prices(direct_vm, coinbase="100", kraken="100", bitstamp="100")
    feed.update("BTC-USD")

    direct_vm.clear_mocks()
    mock_prices(direct_vm, coinbase="150", kraken="150.2", bitstamp="149.9")
    out = feed.update("BTC-USD")
    assert out["status"] == "held"
    info = feed.latest("BTC-USD")
    assert info["value"] == 10000
    assert info["has_pending"] is True

    # an independent second round that sees the same move promotes it
    warp(direct_vm, "2026-11-01T12:05:00Z")
    out = feed.update("BTC-USD")
    assert out["status"] == "committed"
    info = feed.latest("BTC-USD")
    assert info["value"] == 15000
    assert info["round_id"] == 2
    assert info["has_pending"] is False


def test_creator_can_accept_pending(feed, direct_vm, direct_bob):
    mock_prices(direct_vm, coinbase="100", kraken="100", bitstamp="100")
    feed.update("BTC-USD")
    direct_vm.clear_mocks()
    mock_prices(direct_vm, coinbase="50", kraken="50", bitstamp="50")
    feed.update("BTC-USD")

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("not feed admin"):
            feed.accept_pending("BTC-USD")

    feed.accept_pending("BTC-USD")
    assert feed.latest("BTC-USD")["value"] == 5000


def test_paused_feed_rejects_updates(feed, direct_vm):
    feed.set_paused("BTC-USD", True)
    with direct_vm.expect_revert("paused"):
        feed.update("BTC-USD")


def test_history_is_newest_first(feed, direct_vm):
    for i, p in enumerate(["100", "101", "102"]):
        direct_vm.clear_mocks()
        mock_prices(direct_vm, coinbase=p, kraken=p, bitstamp=p)
        warp(direct_vm, f"2026-11-01T12:0{i}:00Z")
        feed.update("BTC-USD")
    rounds = feed.get_rounds("BTC-USD", 2)
    assert [r["value"] for r in rounds] == [10200, 10100]
    assert rounds[0]["round_id"] == 3


def test_page_source_with_grounding(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    c.create_feed("BTC-USD", "Bitcoin spot price in US dollars", 2, 3, 150, 50, 0, 3600,
                  btc_sources(with_page=True))
    mock_prices(direct_vm, coinbase="67250.00", kraken="67260.00", bitstamp=None)
    direct_vm.mock_web(r".*coinmarketcap\.com.*",
                       {"status": 200, "body": "Bitcoin price today is $67,255.31 USD"})
    direct_vm.mock_llm(r".*read a single number.*", '{"value": "67255.31"}')
    c.update("BTC-USD")
    info = c.latest("BTC-USD")
    assert info["sources_used"] == 3
    assert info["value"] == 6725531


def test_page_source_hallucination_is_dropped(direct_vm, direct_deploy, direct_alice):
    direct_vm.sender = direct_alice
    c = direct_deploy(CONTRACT)
    c.create_feed("BTC-USD", "Bitcoin spot price in US dollars", 2, 3, 150, 50, 0, 3600,
                  btc_sources(with_page=True))
    mock_prices(direct_vm, coinbase="67250.00", kraken="67260.00", bitstamp=None)
    direct_vm.mock_web(r".*coinmarketcap\.com.*",
                       {"status": 200, "body": "Bitcoin price today is unavailable"})
    direct_vm.mock_llm(r".*read a single number.*", '{"value": "67255.31"}')
    # the model's number is not on the page, so only two sources count
    with direct_vm.expect_revert("quorum not reached"):
        c.update("BTC-USD")
