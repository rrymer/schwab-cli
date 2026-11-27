"""Credential-free tests for the YAML DocumentLoader boundary."""
import unittest

from schwab_cli.tradeplans.contracts import DocumentLoader
from schwab_cli.tradeplans.yaml_loader import DocumentLoadError, YamlDocumentLoader


class YamlDocumentLoaderTests(unittest.TestCase):
    def setUp(self):
        self.loader = YamlDocumentLoader()

    def test_implements_abc_and_returns_native_objects(self):
        self.assertIsInstance(self.loader, DocumentLoader)
        data = self.loader.load("""
strategy: my_entry
version: 1
symbols:
  spy: SCHWAB:SPY
rules:
  - when: {op: ">", args: [POSITION.spy, 0]}
    then:
      action: CLOSE
      target: spy
      next: END
enabled: true
metadata: null
""")
        self.assertEqual(data["strategy"], "my_entry")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["rules"][0]["then"]["next"], "END")
        self.assertIs(data["enabled"], True)
        self.assertIsNone(data["metadata"])
        self.assertIs(type(data), dict)
        self.assertIs(type(data["rules"]), list)

    def test_quoted_values_comments_and_regular_aliases(self):
        data = self.loader.load("""
symbol: &symbol 'SPY:2026'
copy: *symbol
# regular YAML comment
rules:
  - {action: LOG, message: "confirmed"}
""")
        self.assertEqual(data["symbol"], "SPY:2026")
        self.assertEqual(data["copy"], "SPY:2026")

    def test_duplicate_keys_rejected_at_any_depth(self):
        for source in (
            "a: 1\na: 2\n",
            "a:\n  b: 1\n  b: 2\n",
            "a: 1\na: 1\n",
        ):
            with self.subTest(source=source), self.assertRaises(DocumentLoadError):
                self.loader.load(source)

    def test_invalid_yaml_and_multiple_documents_rejected(self):
        for source in ("a: [1\n", "a: 1\n---\nb: 2\n"):
            with self.subTest(source=source), self.assertRaises(DocumentLoadError):
                self.loader.load(source)

    def test_top_level_must_be_mapping(self):
        for source in ("", "null", "[]", "- a\n- b", "42"):
            with self.subTest(source=source), self.assertRaises(DocumentLoadError):
                self.loader.load(source)

    def test_rejects_unsafe_tags_dates_nonstring_keys_and_nonfinite_values(self):
        for source in (
            "payload: !!python/object/apply:os.system ['echo unsafe']",
            "date: 2026-11-27",
            "3: value",
            "a: .nan",
            "a: .inf",
            "a: -.inf",
        ):
            with self.subTest(source=source), self.assertRaises(DocumentLoadError):
                self.loader.load(source)

    def test_rejects_recursive_alias(self):
        with self.assertRaises(DocumentLoadError):
            self.loader.load("recursive: &r [*r]\n")

    def test_rejects_limits_and_repeated_expansion(self):
        with self.assertRaises(DocumentLoadError):
            YamlDocumentLoader(max_bytes=4).load("a: 1")
        with self.assertRaises(DocumentLoadError):
            YamlDocumentLoader(max_depth=2).load("a:\n  b:\n    c: 1")
        with self.assertRaises(DocumentLoadError):
            YamlDocumentLoader(max_nodes=5).load(
                "a: &a [1, 2, 3]\nb: *a\n"
            )

    def test_constructor_and_type_validation(self):
        for limits in ({"max_bytes": 0}, {"max_nodes": True}, {"max_depth": -1}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                YamlDocumentLoader(**limits)
        for source in (None, b"a: 1", {"a": 1}):
            with self.subTest(source=source), self.assertRaises(TypeError):
                self.loader.load(source)

    def test_load_is_side_effect_free_and_does_not_resolve_keywords(self):
        doc = self.loader.load("""
strategy: invented
rules:
  - when: random_unknown_condition
    then: {action: MAGIC_ORDER, next: END}
""")
        self.assertEqual(doc["rules"][0]["then"]["action"], "MAGIC_ORDER")


if __name__ == "__main__":
    unittest.main()
