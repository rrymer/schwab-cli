"""Credential-free tests for YAML -> validated, typed TradePlan loading."""
import unittest
import tempfile
from pathlib import Path

from schwab_cli.tradeplans.contracts import DocumentLoader, TradePlan, Operation
from schwab_cli.tradeplans.loader import DocumentLoadError, YamlDocumentLoader
from schwab_cli.tradeplans import get_loader
from schwab_cli.tradeplans.semantics import TradePlanSemanticError


VALID = """
strategy:
  name: put_spread_workflow
  version: 1
  symbols:
    spy: {source: "BACKTEST:SPY", timeframe: "1h"}
plan:
  id: example
  entry: open
  blocks:
    open:
      when: {op: "=", args: [{ref: POSITION.spy}, 0]}
      then: {action: BUY, target: spy, sizing: 1, next: close}
    close:
      when: {op: ">", args: [{ref: POSITION.spy}, 0]}
      then: {action: CLOSE, target: spy, next: END}
    END:
      when: {op: "*"}
      then: {action: STOP}
"""


class YamlDocumentLoaderTests(unittest.TestCase):
    def setUp(self):
        self.loader = YamlDocumentLoader()

    def test_load_returns_validated_tradeplan(self):
        self.assertIsInstance(self.loader, DocumentLoader)
        plan = self.loader.load(VALID)
        self.assertIsInstance(plan, TradePlan)
        self.assertEqual(plan.identifier, "example")
        self.assertEqual(plan.strategy.name, "put_spread_workflow")
        self.assertEqual([x.identifier for x in plan.steps], ["open", "close", "END"])
        self.assertEqual(plan.steps[0].actions[0].verb, "BUY")
        self.assertEqual(plan.steps[0].transitions[0].destination, "close")
        self.assertIsInstance(plan.strategy.rules[0].condition, Operation)
        self.assertEqual(plan.strategy.rules[0].condition.references(),
                         frozenset({"POSITION.spy"}))

    def test_reject_unsupported_qkt_or_tradeplan_constructs(self):
        for text in (
            VALID.replace("action: BUY", "action: MAGIC"),
            VALID.replace("sizing: 1", "sizing: -1"),
            VALID.replace("target: spy", "target: missing"),
            VALID.replace("op: \"=\"", "op: eval"),
            VALID.replace("strategy:", "unknown:\nstrategy:", 1),
        ):
            with self.subTest(text=text), self.assertRaises(TradePlanSemanticError):
                self.loader.load(text)

    def test_invalid_graph_rejected(self):
        for text in (
            VALID.replace("next: close", "next: missing"),
            VALID.replace("next: close", "next: open"),
            VALID.replace("next: END", "next: open"),
            VALID.replace("entry: open", "entry: missing"),
            VALID.replace("    END:\n      when: {op: "*"}\n      then: {action: STOP}", "    END:\n      then: {action: STOP}\n    unused:\n      when: {op: "*"}\n      then: {action: STOP}"),
        ):
            with self.subTest(text=text), self.assertRaises(TradePlanSemanticError):
                self.loader.load(text)

    def test_stop_is_canonical_terminal_action(self):
        plan = self.loader.load(VALID)
        terminal = plan.steps[-1]
        self.assertTrue(terminal.terminal)
        self.assertEqual(terminal.identifier, "END")
        self.assertEqual([action.verb for action in terminal.actions], ["STOP"])
        self.assertEqual(terminal.transitions, ())

    def test_stop_may_be_lowercase_but_requires_no_extras(self):
        self.assertTrue(self.loader.load(VALID.replace("action: STOP", "action: stop")).steps[-1].terminal)
        invalid = (
            VALID.replace("then: {action: STOP}", "then: {action: STOP, next: open}"),
            VALID.replace("then: {action: STOP}", "then: {action: STOP, target: spy}"),
            VALID.replace("then: {action: STOP}", "then: {action: STOP, sizing: 1}"),
            VALID.replace("then: {action: STOP}", "then: {action: STOP, message: done}"),
            VALID.replace("    END:\n      then: {action: STOP}",
                          "    END:\n      when: true\n      then: {action: STOP}"),
            VALID.replace("then: {action: STOP}", "type: terminal"),
            VALID.replace("then: {action: STOP}", "then: {action: END}"),
        )
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(TradePlanSemanticError):
                self.loader.load(source)

    def test_unconditional_wildcard_preserves_multiplication(self):
        plan = self.loader.load(VALID)
        self.assertEqual(plan.strategy.rules[-1].condition, Operation("*", ()))
        self.assertEqual(plan.strategy.rules[-1].actions[0].verb, "STOP")
        unconditional = VALID.replace('when: {op: "=", args: [{ref: POSITION.spy}, 0]}',
                                      'when: {op: "*"}')
        self.assertEqual(self.loader.load(unconditional).strategy.rules[0].condition,
                         Operation("*", ()))
        multiplication = VALID.replace(
            'when: {op: "=", args: [{ref: POSITION.spy}, 0]}',
            'when: {op: "*", args: [2, 3]}',
        )
        self.assertEqual(len(self.loader.load(multiplication).strategy.rules[0].condition.operands), 2)

    def test_unconditional_wildcard_rejects_invalid_arity_and_missing_when(self):
        invalid = (
            VALID.replace('when: {op: "*"}', 'when: {op: "*", args: [1]}'),
            VALID.replace('when: {op: "*"}', 'when: {op: "*", args: [1, 2]}'),
            VALID.replace('when: {op: "*"}', 'when: {op: "*", args: "bad"}'),
            VALID.replace('when: {op: "*"}\\n      then: {action: STOP}',
                          'then: {action: STOP}'),
            VALID.replace('when: {op: "*"}', 'when: false'),
        )
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(TradePlanSemanticError):
                self.loader.load(source)

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(DocumentLoadError):
            self.loader.load(VALID + "\nstrategy: {}\n")

    def test_unsafe_yaml_and_invalid_documents_rejected(self):
        for source in (
            "", "null", "[]", "x: 1\n---\ny: 2",
            "payload: !!python/object/apply:os.system ['echo unsafe']",
            "x: .nan", "x: 2026-11-27", "3: value",
            "recursive: &r [*r]", "bad: [1",
        ):
            with self.subTest(source=source), self.assertRaises(DocumentLoadError):
                self.loader.load(source)

    def test_bounded_resources_and_validation(self):
        with self.assertRaises(DocumentLoadError):
            YamlDocumentLoader(max_bytes=8).load(VALID)
        with self.assertRaises(DocumentLoadError):
            YamlDocumentLoader(max_nodes=3).load(VALID)
        with self.assertRaises(DocumentLoadError):
            YamlDocumentLoader(max_depth=2).load(VALID)
        with self.assertRaises(TypeError):
            self.loader.load(b"strategy: x")
        with self.assertRaises(ValueError):
            YamlDocumentLoader(max_nodes=True)


    def test_notebook_flow_with_str_and_pathlike(self):
        with tempfile.TemporaryDirectory() as folder:
            for ext in (".yaml", ".yml", ".YAML"):
                path = Path(folder) / ("myplan" + ext)
                path.write_text(VALID, encoding="utf-8")
                with self.subTest(ext=ext):
                    self.assertIsInstance(get_loader(str(path)).load(), TradePlan)
                    self.assertEqual(get_loader(path).load().identifier, "example")

    def test_explicit_override_selects_loader_for_unknown_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "myplan.plan"
            path.write_text(VALID, encoding="utf-8")
            self.assertEqual(get_loader(path, loader="yaml").load().identifier, "example")
            self.assertEqual(get_loader(path, loader=YamlDocumentLoader).load().identifier, "example")
            custom = YamlDocumentLoader()
            self.assertEqual(get_loader(path, loader=custom).load().identifier, "example")

    def test_custom_loader_is_injected_and_receives_file_content(self):
        class StubLoader(DocumentLoader):
            def load(self, source: str) -> TradePlan:
                self.received = source
                return YamlDocumentLoader().load(source)

        stub = StubLoader()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "anything.custom"
            path.write_text(VALID, encoding="utf-8")
            bound = get_loader(path, loader=stub)
            self.assertFalse(hasattr(stub, "received"))  # factory doesn't read.
            self.assertEqual(bound.load().identifier, "example")
            self.assertEqual(stub.received, VALID)

    def test_unknown_extension_bad_override_and_missing_file(self):
        with self.assertRaisesRegex(ValueError, "Unsupported TradePlan format"):
            get_loader("myplan.json")
        with self.assertRaises(TypeError):
            get_loader("myplan.yaml", loader=object())
        with self.assertRaises(TypeError):
            get_loader(b"myplan.yaml")
        with self.assertRaises(FileNotFoundError):
            get_loader("unavailable.yaml").load()

    def test_bound_loader_rejects_oversized_and_non_utf8_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "myplan.yaml"
            path.write_text(VALID, encoding="utf-8")
            with self.assertRaisesRegex(DocumentLoadError, "exceeds"):
                get_loader(path, loader=YamlDocumentLoader(max_bytes=64)).load()
            path.write_bytes(bytes((255, 254, 250)))
            with self.assertRaisesRegex(DocumentLoadError, "UTF-8"):
                get_loader(path).load()

    def test_path_is_never_confused_with_yaml_document_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "myplan.yaml"
            path.write_text(VALID, encoding="utf-8")
            self.assertNotIsInstance(get_loader(path), TradePlan)
            self.assertEqual(get_loader(path).load().strategy.name, "put_spread_workflow")

    def test_loader_does_not_execute_actions(self):
        plan = self.loader.load(VALID)
        self.assertEqual(plan.steps[0].actions[0].arguments, {"sizing": 1})


if __name__ == "__main__":
    unittest.main()
