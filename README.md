# quorum-feed

A multi-source numeric oracle written as a GenLayer Intelligent Contract.

Most "oracle" examples on GenLayer fetch one page, ask a model for a number and
run `strict_eq` on the answer. That works in a demo and breaks in practice:
prices tick between the leader's fetch and the validators' fetch, a single
source goes down, a page changes its layout, or the model returns a number that
is not on the page at all.

`QuorumFeed` treats a value as trustworthy only when several independent
sources agree on it, and lets validators accept a leader's round when their own
independent observation lands inside a tolerance band instead of requiring
byte-equal output. On top of that it adds the things a consumer contract
actually needs: staleness checks, round history and a circuit breaker for
suspicious jumps.

Deployed on GenLayer Studio (studionet):
[`0xcee1D03bc2558a07BaF7EC853A08D1CBceB70420`](https://explorer-studio.genlayer.com/address/0xcee1D03bc2558a07BaF7EC853A08D1CBceB70420)
with a live `BTC-USD` feed reading Coinbase, Kraken and Bitstamp.

## How a round works

```
update(feed_id)
  │
  ├─ leader, inside run_nondet
  │    for each source:  fetch ─▶ extract ─▶ fixed-point int   (failures are skipped)
  │    aggregate():      median ─▶ drop outliers > max_spread_bps ─▶ median of survivors
  │                      fewer than `quorum` survivors ─▶ UserError
  │    returns {value, mask, spread_bps}
  │
  ├─ each validator, inside run_nondet
  │    repeats the whole observation on its own
  │    compares against the leader (see below)
  │
  └─ deterministic part
       circuit breaker ─▶ commit round / hold as pending
```

### Sources

Each source is `{url, kind, selector}`:

| kind   | how the value is read                                                                 |
|--------|---------------------------------------------------------------------------------------|
| `json` | `gl.nondet.web.get`, then a dotted path into the body (`result.XXBTZUSD.c.0`, `data.amount`, negative list indexes allowed). No LLM involved. |
| `page` | `gl.nondet.web.render(mode="text")` and an LLM extraction guided by the selector hint. |

`page` sources are grounded: the integer part of whatever number the model
returns must appear as a digit run in the rendered page text (thousands
separators are tolerated). A model that computes, rounds or invents a number
simply makes that source count as "no answer" for the round.

Values are converted with `Decimal` to fixed-point integers using the feed's
`decimals`, so no floats ever reach storage or the comparison logic.

### Consensus

The non-deterministic block uses `gl.vm.run_nondet_unsafe` with a custom
validator rather than one of the built-in equivalence principles, because the
right notion of "same answer" here is numeric:

1. **Value within tolerance.** The validator computes its own aggregate and
   accepts if `|leader - mine| / mine <= tolerance_bps`. Prices that moved a few
   basis points between fetches still reach consensus.
2. **Quorum is real.** The leader's `mask` (bitmap of sources that survived
   outlier filtering) must have at least `quorum` bits set and no bits outside
   the configured source list.
3. **Same sources, mostly.** The leader's mask and the validator's mask must
   overlap in at least `max(1, quorum - 1)` sources. One source may flap between
   the two fetches; a leader that built its value from a different set of
   sources than everyone else gets rejected.
4. **Errors are judged too.** If the leader reports "quorum not reached", the
   validator only agrees when it also fails to reach quorum. A leader cannot
   stall a healthy feed by claiming an outage.

`aggregate()` and `within_tolerance()` are plain functions, so both sides apply
exactly the same arithmetic. Even-length medians use floor division to stay
integer and identical on every node.

### Circuit breaker

If `max_jump_bps > 0` and a new round moves more than that from the last
committed value, it is not committed. It is stored as *pending* instead. The
next independent round that lands within `tolerance_bps` of the pending value
confirms the move and commits it; a one-off bad print never reaches consumers.
The feed creator can also force it through with `accept_pending`.

## State

```python
feeds:    TreeMap[str, Feed]               # config + latest committed round + pending slot
sources:  TreeMap[str, DynArray[Source]]   # per-feed source list
history:  TreeMap[str, Round]              # "<feed_id>:<round_id>", last 64 rounds kept
feed_ids: DynArray[str]
owner:    Address                          # contract-level admin
```

`Feed` keeps the latest round inline so `latest()` / `read_fresh()` are single
lookups. History is keyed by round id and pruned as new rounds arrive, so
storage per feed is bounded.

## Interface

| method | kind | notes |
|---|---|---|
| `create_feed(feed_id, description, decimals, quorum, max_spread_bps, tolerance_bps, max_jump_bps, heartbeat, sources)` | write | anyone can create a feed and becomes its admin |
| `update(feed_id)` | write | runs a consensus round; returns `{status: committed|held, ...}` |
| `set_thresholds(feed_id, quorum, max_spread_bps, tolerance_bps, max_jump_bps, heartbeat)` | write | feed admin |
| `set_paused(feed_id, paused)` | write | feed admin |
| `accept_pending(feed_id)` | write | feed admin, overrides the circuit breaker |
| `latest(feed_id)` | view | value, decimals, round id, age, `stale`, pending info |
| `read_fresh(feed_id)` | view | value, or reverts with `feed is stale` |
| `get_rounds(feed_id, limit)` | view | newest first |
| `get_feed(feed_id)` / `list_feeds()` | view | configuration |

Feed admin is the feed creator or the contract owner.

### Example feed

```json
[
  {"url": "https://api.coinbase.com/v2/prices/BTC-USD/spot", "kind": "json", "selector": "data.amount"},
  {"url": "https://api.kraken.com/0/public/Ticker?pair=XBTUSD", "kind": "json", "selector": "result.XXBTZUSD.c.0"},
  {"url": "https://www.bitstamp.net/api/v2/ticker/btcusd/", "kind": "json", "selector": "last"}
]
```

```bash
genlayer network set studionet
genlayer deploy --contract contracts/quorum_feed.py

genlayer write <address> create_feed --args BTC-USD "Bitcoin spot price in US dollars" \
  2 2 150 50 1000 3600 '<sources json above>'
genlayer write <address> update --args BTC-USD
genlayer call  <address> latest --args BTC-USD
```

Parameters in that call: 2 decimals, quorum 2 of 3, sources further than 1.5%
from the median are dropped, validators accept within 0.5%, moves above 10%
need confirmation, stale after one hour.

On the studionet deployment above, the first `update` was accepted with three
validators agreeing and committed `7381438` (73,814.38 USD) from all three
sources.

### Reading it from another contract

```python
feed = gl.get_contract_at(Address(QUORUM_FEED))
price = feed.view().read_fresh("BTC-USD")   # reverts if older than the heartbeat
```

## Choosing parameters

- `max_spread_bps` decides what counts as an outlier. Set it from how far
  honest sources normally drift apart (for liquid spot prices, 50–200 bps).
- `tolerance_bps` decides consensus. It should cover the movement you expect
  between leader and validator fetches, and should be tighter than
  `max_spread_bps`.
- `quorum` should usually be a strict majority of the sources. With `quorum`
  equal to the source count, a single outage halts the feed.
- `max_jump_bps = 0` disables the circuit breaker.
- `page` sources cost an LLM call per node per round. Prefer `json` sources
  where an API exists and use `page` sources to add diversity.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

genvm-lint check contracts/quorum_feed.py
pytest -q
```

The tests run in gltest direct mode with mocked web responses and LLM output:

- `tests/test_aggregate.py` – median, outlier filtering, fixed-point parsing,
  JSON path walking and the grounding check.
- `tests/test_feed.py` – feed lifecycle, quorum failure, staleness, the circuit
  breaker, history and `page` sources (including a hallucinated value being
  dropped).
- `tests/test_validator.py` – the validator function on its own: drift inside
  and outside tolerance, forged values, undersized or out-of-range masks, a
  flapping source, and false versus real "quorum not reached" claims.

## License

MIT
