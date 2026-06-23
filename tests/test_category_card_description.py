"""Regression tests for client-facing GEO category card copy."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _load_create_report():
    path = BACKEND_ROOT / "create-report.py"
    name = "create_report_mod"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestCategoryCardDescription(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cr = _load_create_report()

    def test_technical_copy_drops_internal_jargon(self):
        text = self.cr.category_card_description("technical_setup", 78.9)
        self.assertNotIn("Weighted 25/25/20/15/15", text)
        self.assertNotIn("SSR/raw HTML", text)
        self.assertIn("Measures whether search engines and AI tools", text)
        self.assertIn("technical foundation is solid", text.lower())

    def test_example_band_ai_visibility(self):
        text = self.cr.category_card_description("ai_visibility", 41.1)
        self.assertIn("Measures whether AI search tools", text)
        self.assertIn("may struggle to recognise the brand", text.lower())

    def test_example_band_content(self):
        text = self.cr.category_card_description("content_structure", 42.4)
        self.assertIn("helpful, trustworthy", text.lower())
        self.assertIn("not yet clear, trusted", text.lower())

    def test_no_legacy_phrases_in_any_category(self):
        for key, score in (
            ("ai_visibility", 50.0),
            ("technical_setup", 50.0),
            ("content_structure", 50.0),
        ):
            with self.subTest(key=key):
                t = self.cr.category_card_description(key, score)
                self.assertNotIn("Covers brand clarity", t)
                self.assertNotIn("retrieval-friendly structure", t)
                self.assertNotIn("Combines trust-and-expertise", t)


if __name__ == "__main__":
    unittest.main()
