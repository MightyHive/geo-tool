"""Smoke tests for client-facing report copy helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import report_copy as rc


class TestReportCopy(unittest.TestCase):
    def test_merged_robots_rewrite(self):
        t = rc.client_friendly_text(
            "Merged robots.txt suggestion is in this audit folder under /tmp/foo."
        )
        self.assertIn("developer", t.lower())
        self.assertNotIn("audit folder", t.lower())

    def test_bytespider_policy_bucket(self):
        t = rc.client_friendly_text("Consider blocking Bytespider for policy reasons.")
        h, r = rc.classify_action_horizon_and_rank(t)
        self.assertEqual(h, "policy")

    def test_prepare_dedupes_discovery_family(self):
        raw = [
            "Improve discovery: live llms.txt, reachable Sitemap from robots.",
            "No live llms.txt at origin.",
            "Ensure robots.txt declares a live sitemap URL for Tier-1 check.",
        ]
        _q, _m, _s, _p, narrative = rc.prepare_report_priorities(raw)
        self.assertEqual(len(narrative), 1)

    def test_manual_caveat_detection(self):
        self.assertTrue(rc.is_manual_caveat("News corroboration is not fully automated—validate manually."))
        self.assertFalse(rc.is_manual_caveat("Publish llms.txt at the site root."))


if __name__ == "__main__":
    unittest.main()
