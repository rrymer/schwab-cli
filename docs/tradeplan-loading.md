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
