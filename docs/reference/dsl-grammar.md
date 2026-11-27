# qkt DSL grammar reference for Schwab CLI

> **Status:** Upstream-language reference; **not** an implemented Schwab CLI grammar or a TradePlans specification.
>
> **Source of truth:** [qkt DSL grammar](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl-grammar.md), supported by the [qkt DSL reference](https://github.com/elitekaycy/qkt/tree/main/docs/reference/dsl) and its compiler-checked examples. Read from upstream `main` on 2026-11-27. Upstream is a living specification; verify against a pinned commit before implementation.
>
> **Scope:** This is a compact, independently written implementation reference for the existing qkt language and the specific semantic boundaries that matter to Schwab CLI. Examples and grammar below are illustrative summaries; consult the upstream docs for exhaustive accepted forms and parser diagnostics. **Uppercase keywords are qkt constructs; they are not Schwab CLI commands.**

## 1. Files and top-level ordering

qkt's top-level document is a `STRATEGY` or a `PORTFOLIO`. In a strategy, sections appear **in this order**:

```text
STRATEGY <name> VERSION <integer>
[ DEFAULTS { ... } ]
SYMBOLS
    <alias> = <BROKER>:<symbol> EVERY <timeframe> [ WARMUP <N> BARS ]
[ PARAM <name> = <literal> ] ...
[ LET <name> = <expression> [, ...] ] ...
[ SCHEDULE ... ]
[ SEQUENCE ... ]
[ RULES
    WHEN <condition>
    THEN <action> [ ; <action> ... ]
    ...
]
```

`SYMBOLS` is required for compilation. `RULES` is parser-optional; moving `LET` or other ordered sections after `RULES` is invalid. Identifiers may begin with a letter or underscore.

A portfolio instead has the form:

```text
PORTFOLIO <name> VERSION <integer> [ CAPITAL <number> ]
[ SYMBOLS ... ]
IMPORT '<path>' AS <alias> [ HOLD ]
[ REGIMES ... ]
[ ALLOCATE METHOD regime_weighted [ REBALANCE EVERY <duration> ] ... ]
RULES
    [ WHEN <condition> ] RUN <alias>
      [ WEIGHT <number> ] [ OVERRIDE { <parameter> = <literal>, ... } ]
```

A `PORTFOLIO` can activate/deactivate child strategies and override their declared `PARAM` values. A `HOLD` import keeps a child's positions on deactivation; without it, deactivation flattens them. Portfolios do not use strategy `LET` or `DEFAULTS`.

References: [grammar](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl-grammar.md), [portfolio](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/portfolio.md).

## 2. Streams, parameters and reusable expressions

A `SYMBOLS` declaration binds a local alias to a broker symbol and candle frequency:

```qkt
STRATEGY trend_example VERSION 1
SYMBOLS
    asset = BACKTEST:SPY EVERY 1h WARMUP 50 BARS
PARAM fast = 9
PARAM slow = 21
LET fastMA = ema(asset.close, fast), slowMA = ema(asset.close, slow)
RULES
    WHEN fastMA CROSSES ABOVE slowMA AND POSITION.asset = 0
    THEN BUY asset SIZING 1
```

The broker prefix is resolved against a configured broker registry; `BACKTEST` is built in. Timeframe units are `s`, `m`, `h`, and `d` (the grammar explicitly says **no `1w`**). `WARMUP` blocks rules depending on that stream until enough closed candles exist. `PARAM` defines an overridable scalar default; `LET` names a reusable per-evaluation expression.

qkt also documents non-standard streams for option chains, option roots, market data and synthetic series. For options, for example, `OPTIONS:<VENUE>.<ROOT>` is a feed of a root, while `CHAIN:<VENUE>.<ROOT>.<metric>.<tenor>` exposes read-only derived chain metrics such as `atm_iv` and `skew_25d`. These require an instrument catalog and appropriate feeds; they do **not** imply native Schwab support.

References: [streams](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/streams.md), [chain](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/chain.md).

## 3. Rules and conditions

The primary rule form is:

```text
WHEN <boolean condition>
THEN <action> [ ; <action> ... ]
```

Actions in a single rule are separated by **semicolons**, not newlines. qkt evaluates conditions within its strategy runtime. Supported expression families include:

| Family | Documented constructs |
| --- | --- |
| Arithmetic | `+`, `-`, `*`, `/`, unary minus; use `mod(a,b)`, not `%` |
| Comparisons | `<`, `<=`, `>`, `>=`, `=`, `==`, `!=`, `<>` (string equality/inequality is supported) |
| Boolean | `AND`, `OR`, `NOT` |
| Transitions | `CROSSES ABOVE`, `CROSSES BELOW` |
| Membership and null | `BETWEEN ... AND ...`, `IN [ ... ]`, `IS NULL`, `IS NOT NULL` |
| Conditional expression | `CASE WHEN ... THEN ... ELSE ... END` (requires `ELSE`) |
| Literals | Numbers, `TRUE`/`FALSE`, quoted strings |
| Indicators | `ema`, `sma`, `rsi`, `atr`, and additional documented indicator functions |

Stream field references include `asset.open`, `.high`, `.low`, `.close`, `.volume`, `.bid`, `.ask`, `.timestamp`, historical `.close[n]`, and venue/contract fields where data capabilities support them. Account references include `ACCOUNT.equity`, `.balance`, and P&L fields.

**Position vocabulary is explicit**, not `position_open`:

```qkt
WHEN POSITION.asset = 0
THEN BUY asset SIZING 1
```

`POSITION.<stream>` denotes signed quantity. Documented attributes include `.qty`, `.entry_price`, `.pnl`, `.unrealized_pnl`, and `.realized_pnl`. Option structure aliases have specialized fields, described below.

References: [conditions](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/conditions.md), [expressions](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/expressions.md), [indicators](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/indicators.md).

## 4. Actions, sizing and order clauses

Core documented action families:

| Action | Meaning in qkt |
| --- | --- |
| `BUY <stream> ...`, `SELL <stream> ...` | Open/add exposure |
| `CLOSE <stream>` | Close the position on one stream |
| `CLOSE_ALL` / `FLATTEN` | Close all positions |
| `RESIZE <stream> TO <sizing>` | Adjust to a target position size |
| `CANCEL <stream>`, `CANCEL_ALL` | Cancel pending orders |
| `OCO_ENTRY { ..., ... }` | One-cancels-other entry pair |
| `LOG [WARN|ERROR|DEBUG] "..."` | Structured logging |
| `LATCH ...` | Directional conditional-entry construct |

`BUY` and `SELL` require `SIZING`, either directly on the action or inherited from `DEFAULTS`. Sizing forms include fixed quantity, `PCT OF EQUITY`, `PCT OF BALANCE`, `USD`, `RISK`, and `POSITION.<alias>`; risk-based forms have additional constraints.

Documented order clauses include `ORDER_TYPE = MARKET`, `LIMIT AT <price>`, `STOP AT <price>`, stop-limit, trailing forms, and `TIF GTC|IOC|FOK|DAY|GTD` (GTD with an expiration expression). Other entry modifiers include `STACK`, `TIMES`, and `EXIT AFTER`.

A `BRACKET` encloses **both** stop-loss and take-profit; qkt rejects a bare `STOP_LOSS` attached directly to `BUY`/`SELL`:

```qkt
WHEN asset.close > asset.open
THEN BUY asset SIZING 1 BRACKET {
    STOP_LOSS BY 1 PCT,
    TAKE_PROFIT BY 2 PCT
}
```

Bracket levels can use absolute prices (`AT`), distance (`BY`), percentages, and reward/risk targets (`RR`). qkt distinguishes broker-native brackets from engine-managed exits; its behavior does not automatically carry over to a Schwab adapter.

References: [actions](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/actions.md), [sizing](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/sizing.md), [bracket](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/bracket.md), [OTO](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/oto.md).

## 5. Existing conditional and staged constructs

**Do not mistake these for the proposed TradePlans execution DAG.** qkt already documents several forms of conditional progression:

- `SEQUENCE <name> ON <stream> { STAGE ... }` describes **2–8 ordered candle-confirmed stages**, with optional `WITHIN` timeouts and stateful progress. Accessors include `SEQUENCE.<name>.complete` and per-stage snapshots. This is a signal setup, not a graph of arbitrarily dependent broker actions.
- `ON_FILL`, `ON_STOP`, `ON_TP`, and `ON_CLOSE` are documented lifecycle callbacks on entry actions. They should be evaluated for expressing fill-dependent actions before inventing a successor mechanism.
- `OCO`, `OCO_ENTRY`, brackets, and one-triggers-other constructs already express common contingent-order relationships.
- `FOR EACH <variable> IN [<streams>] DO WHEN ... THEN ...` expands rules across streams.

One existing multi-stage pattern:

```qkt
SEQUENCE confirmation ON asset {
    STAGE pullback: asset.low < asset.low[1]
    STAGE recovery WITHIN 30m: asset.close > asset.open
}
RULES
    WHEN SEQUENCE.confirmation.complete
    THEN BUY asset SIZING 1
```

This example illustrates the source's `SEQUENCE` shape; production signal design must account for its edge-trigger semantics and available bar history.

References: [sequence](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/sequence.md), [actions](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/actions.md), [OTO](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/oto.md).

## 6. Multi-leg option structures

qkt has an explicit option-structure action rather than requiring individually handwritten option-leg orders:

```text
OPEN <alias> = OPTIONS ON <VENUE>:<ROOT> { <leg>, ... } SIZING <sizing>
<leg> := BUY|SELL CALL|PUT DELTA <0..1> (DTE <n> TO <m> | SAME EXPIRY)
```

The upstream example uses a put credit spread selected from an option chain:

```qkt
STRATEGY credit_spread_example VERSION 1
SYMBOLS
    chain = OPTIONS:DERIBIT.BTC_USDC EVERY 1h,
    iv = CHAIN:DERIBIT.BTC_USDC.atm_iv.30d EVERY 1h
RULES
    WHEN iv.close > 55 AND POSITION.ps = 0
    THEN OPEN ps = OPTIONS ON DERIBIT:BTC_USDC {
        SELL PUT DELTA 0.25 DTE 30 TO 45,
        BUY PUT DELTA 0.10 SAME EXPIRY
    } SIZING 1 PCT RISK
```

Structure references include `POSITION.ps`, `.credit`, `.max_loss`, `.pnl`, `.pnl_pct`, `.dte`, `.delta`, `.gamma`, `.vega`, and `.theta`. The upstream specification describes field availability and missing quotes; these distinctions need preservation during adaptation.

**Important limitation:** This documented form selects contracts by delta/DTE. It does not establish an accepted fixed-strike form for a Schwab `UNH 350/345` vertical, nor does it by itself define the finite, one-shot TradePlans execution graph. Those are integration design questions, not existing qkt syntax.

Reference: [option structures](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/structures.md).

## 7. What is *not* existing qkt grammar

The following concepts are **proposed Schwab CLI TradePlans extensions**, not verified qkt tokens:

| Proposed construct | Status |
| --- | --- |
| `position_open` | **Not qkt vocabulary**; use documented `POSITION.<alias>` forms where semantically appropriate |
| `then: { next: manage }` | Proposed YAML/TradePlans successor reference |
| `next: END` / `next: ABORT` | Proposed terminal transition semantics |
| Named finite action DAG with every path terminating | **TradePlans invariant**, not guaranteed by ordinary qkt `RULES` |
| Independent pre-submission safeguards report | Schwab CLI feature, not qkt parser guarantee |
| Schwab order idempotency, uncertain POST recovery | Schwab CLI runtime responsibility |

If adopted, the grammar change must be explicitly versioned, documented, and validated. We should **not** replace qkt's existing `THEN` semantics silently: qkt rules naturally recur as market data arrives. TradePlans instead needs finite one-shot action-node activation with durable state, bounded order count, explicit successor references, and abort semantics.

Use separate validation stages:

1. Load user-authored YAML with an established safe YAML loader (no custom YAML/JSON parser).
2. Convert native Python `dict`/`list`/scalar structures into a versioned, qkt-inspired typed semantic representation; **no JSON text roundtrip**.
3. Validate names, types, references, supported trading vocabulary, and condition/action semantics.
4. Construct a `networkx.DiGraph` for explicit TradePlans successors and reject missing nodes, unreachable nodes, cycles, ambiguous branches and nonterminating paths.
5. Enforce safeguards and risk budgets independently before order submission.
6. Let the order runtime manage partial fills, broker-held vs locally held conditions, reconciliation, idempotency and restart.

**Parser note:** qkt's actual parser/AST is implemented in Kotlin. A YAML-to-AST mapping is **our adapter**, not a documented upstream JSON AST standard. Do not claim interoperability or faithful qkt parsing without conformance fixtures against the upstream parser.

## 8. Recommended conformance fixtures

Before implementing a qkt-compatible TradePlans semantic layer, test each supported upstream construct against a pinned qkt revision:

- Accepted `STRATEGY` and `PORTFOLIO` section order, aliases and references.
- Rejected missing `SYMBOLS`, unknown stream aliases and entries with no available sizing.
- `WHEN` operator precedence and expression typing, `LET`, `PARAM`, and `CASE`.
- Account/position fields and unavailable market-data values.
- Order types, brackets, partial-fill callbacks and option structure completeness.
- Sequence progression, timeouts and no-lookahead behavior.
- TradePlans-specific successor DAG validation and independent order-safety tests.

## 9. Primary upstream references

- [One-page DSL grammar](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl-grammar.md)
- [Full DSL index](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/index.md)
- [Strategy block](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/strategy-block.md)
- [Conditions](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/conditions.md)
- [Expressions and position references](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/expressions.md)
- [Actions and callbacks](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/actions.md)
- [Sequences](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/sequence.md)
- [Option structures](https://github.com/elitekaycy/qkt/blob/main/docs/reference/dsl/structures.md)
- [Tested put-spread strategy](https://github.com/elitekaycy/qkt/blob/main/attestation/cases/derivatives/option-put-spread-open-close/strategy.qkt)
