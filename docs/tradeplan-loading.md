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
      type: terminal
```

This can be parsed and validated, but **not executed**. The interpreter
currently supports a restricted action and expression vocabulary, explicit
named successors, and terminal blocks. It does not yet implement qkt option
structures, qkt callbacks, full condition semantics, broker execution, or
runtime safeguards.

Invalid YAML raises `DocumentLoadError`; unsupported semantic forms or
invalid DAG structure raise `TradePlanSemanticError`; unreadable or missing
files raise normal filesystem exceptions. File contents are never interpreted
as Python code.
