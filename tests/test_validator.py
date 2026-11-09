"""
Exercise the validator half of the consensus block directly.

Each test lets the leader commit with one set of mocked prices, then swaps the
mocks to model what a validator would see when it fetches a moment later.
"""

from tests.conftest import mock_prices


def _leader_round(feed, vm, prices):
    mock_prices(vm, *prices)
    feed.update("BTC-USD")


def test_validator_agrees_within_tolerance(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    direct_vm.clear_mocks()
    mock_prices(direct_vm, "67270", "67280", "67255")  # ~0.03% drift
    assert direct_vm.run_validator() is True


def test_validator_rejects_outside_tolerance(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    direct_vm.clear_mocks()
    mock_prices(direct_vm, "67700", "67710", "67690")  # ~0.67% away
    assert direct_vm.run_validator() is False


def test_validator_rejects_forged_value(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    forged = {"value": 7000000, "mask": 0b111, "spread_bps": 0}
    assert direct_vm.run_validator(leader_result=forged) is False


def test_validator_rejects_mask_below_quorum(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    claimed = {"value": 6725000, "mask": 0b001, "spread_bps": 0}
    assert direct_vm.run_validator(leader_result=claimed) is False


def test_validator_rejects_unknown_source_bits(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    claimed = {"value": 6725000, "mask": 0b1011, "spread_bps": 0}
    assert direct_vm.run_validator(leader_result=claimed) is False


def test_validator_tolerates_one_flapping_source(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    direct_vm.clear_mocks()
    mock_prices(direct_vm, "67250", None, "67244")
    assert direct_vm.run_validator() is True


def test_validator_disputes_false_quorum_failure(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    # leader claims it could not reach quorum, but this validator can
    assert direct_vm.run_validator(leader_error=Exception("quorum not reached")) is False


def test_validator_confirms_real_outage(feed, direct_vm):
    _leader_round(feed, direct_vm, ("67250", "67260", "67240"))
    direct_vm.clear_mocks()
    mock_prices(direct_vm, "67250", None, None)
    assert direct_vm.run_validator(leader_error=Exception("quorum not reached")) is True
