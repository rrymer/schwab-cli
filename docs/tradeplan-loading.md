# Loading a TradePlan from a notebook

The public entry point is `get_loader(path).load()`. It returns a validated
`TradePlan` object, **not** a YAML mapping, and it never submits orders.

```python
from pathlib import Path
from schwab_cli.tradeplans import get_loader, TradePlan

plan: TradePlan = get_loader("myplan.yaml").load()
# or
plan = get_loader(Path("myplan.yml")).load()

print(plan.identifier)
print(plan.strategy.name)
print([step.identifier for step in plan.steps])
```

`get_loader()` selects `YamlDocumentLoader` for `.yaml` and `.yml`
case-insensitively. Override automatic selection when the extension is unusual:

```python
from schwab_cli.tradeplans import YamlDocumentLoader, get_loader

plan = get_loader("myplan.plan", loader="yaml").load()
plan = get_loader("myplan.plan", loader=YamlDocumentLoader).load()
plan = get_loader(
    "myplan.plan",
    loader=YamlDocumentLoader(max_bytes=256_000),
).load()
```

A `DocumentLoader` subclass can also be supplied as an explicit override.
The factory binds a filesystem path to that loader without opening the file.
Calling `.load()` opens and reads the file as bounded UTF-8 text and delegates
to the supplied `DocumentLoader`.

For cases involving YAML **source text** already in memory, retain the
lower-level interface:

```python
plan = YamlDocumentLoader().load(yaml_text)
```

## Example `myplan.yaml`

This is the **currently implemented qkt-inspired YAML subset**, not literal
qkt `.qkt` syntax and not a complete qkt implementation.

```yaml
strategy:
  name: example_workflow
  version: 1
  symbols:
    spy:
      source: BACKTEST:SPY
      timeframe: 1h

plan:
  id: example
  entry: open
  blocks:
    open:
      when:
        op: "="
        args:
          - ref: POSITION.spy
          - 0
      then:
        action: BUY
        target: spy
        sizing: 1
        next: close

    close:
      when:
        op: ">"
        args:
          - ref: POSITION.spy
          - 0
      then:
        action: CLOSE
        target: spy
        next: END

    END:
      when:
        op: "*"
      then:
        action: STOP
```

This can be parsed and validated, but **not executed**. The interpreter
currently supports a restricted action and expression vocabulary, explicit
named successors, and terminal `STOP` actions. Every block requires `when`; use `when: {op: "*"}` for unconditional execution. In a `when` clause, zero-argument `*` is the unconditional condition, whereas `*` with two arguments remains multiplication. A `STOP` block must use the unconditional condition and cannot specify `next`, a target, sizing, or a message. It does not yet implement qkt option
structures, qkt callbacks, full condition semantics, broker execution, or
runtime safeguards.

Invalid YAML raises `DocumentLoadError`; unsupported semantic forms or
invalid DAG structure raise `TradePlanSemanticError`; unreadable or missing
files raise normal filesystem exceptions. File contents are never interpreted
as Python code.


## Read-only WHEN monitoring

```bash
schwab monitor plan --plan ./myplan.yaml --once
schwab monitor plan --plan ./myplan.yaml --interval 5
schwab monitor plan --plan ./myplan.yaml --step close --once
```

The command polls the current **named** step (entry by default). It prints an
observation (`triggered`, `action`, and `next_step`) but **never executes,
advances, or persists** the workflow. Conditions must evaluate to a boolean;
missing/stale-unavailable inputs are errors, not false confirmations.

Live data references in this initial adapter are deliberately limited:
`strategy.symbols.<alias>.source: SCHWAB:<ticker>` exposes
`<alias>.close` (from Schwab's current quote `lastPrice`/mark) and
`POSITION.<alias>` (net quantity from the single account snapshot).
`BACKTEST:` streams do **not** silently become live feeds.
This early version does not verify exchange timestamps or quote freshness,
and is **not suitable as a live execution trigger** without additional safeguards.
The evaluation engine also accepts explicitly supplied immutable context
mappings for isolated tests, simulation and future data adapters.

Live quote streams must specify `timeframe: quote`. Candle timeframes such
as `1h` are not implemented in this adapter and are rejected rather
than silently mapped to spot prices.


## Typed qkt-inspired action semantics

`Action` remains an abstract interface in `contracts.py`; concrete semantic
implementations and the YAML compilers are in `semantics.py`. `Trade(Action)`
is itself abstract and is the common superclass for instrument-targeted
`Buy`, `Sell`, `Close` and `Resize`. Non-trade actions include
`Cancel`, `CancelAll`, `CloseAll`, `Flatten`, `Log`,
`OcoEntry`, and the TradePlan-only terminal `Stop`. More complex qkt
intent is represented by `OpenOptions` and `Latch`, but neither implies
implemented multi-leg or conditional execution at Schwab.

`ExpressionCompiler` builds `Literal`/`Reference`/`Operation` objects,
`ActionCompiler` builds concrete typed actions, and
`QktYamlPlanCompiler` combines actions and conditions into the finite
TradePlan graph. They perform no network I/O or order submission.

A BUY entry may specify typed qkt order and sizing clauses:

```yaml
then:
  action: BUY
  target: spy
  sizing: {unit: USD, value: 1000}
  order:
    type: LIMIT
    limit_price: {ref: spy.close}
    tif: DAY
  bracket:
    stop_loss: 590
    take_profit: 630
  next: END
```

Currently accepted `order.type` values: `MARKET`, `LIMIT`, `STOP`,
`STOP_LIMIT`, `TRAILING_BY`, `TRAILING_PCT`. Their price fields are
`limit_price`, `stop_price`, and `trail`, each parsed as an
`Expression`; `tif` supports `DAY`, `GTC`, `IOC`, `FOK`, or
`GTD` with a mandatory `until` expression. The YAML names are our
Python adapter design, **not** an upstream qkt AST serialization format.

`SELL` has upstream qkt short-exposure semantics, **not** the existing
Schwab CLI `SELL` meaning of selling long holdings. Any later broker adapter
must resolve position effect explicitly. `CLOSE` is a market flatten in qkt,
so this compiler does not accept an entry order clause on `CLOSE`.

The monitor merged in #50 remains read-only. This PR **does not enable
`positions add --plan --submit`**; PR #51 is superseded. Runtime capabilities,
fill progression, order risk checks, and option structure resolution require
separate implementation.
