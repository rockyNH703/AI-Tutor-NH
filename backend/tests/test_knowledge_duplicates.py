from __future__ import annotations

import unittest

from app.knowledge.duplicates import DuplicateClass, classify, normalize_page


class DuplicateTests(unittest.TestCase):
    def test_exact_bytes_are_the_only_automatic_exact_duplicate(self):
        evidence = classify("a" * 64, "a" * 64, ["Page 1\nOSPF"], ["Page 9\nchanged"])
        self.assertEqual(evidence.classification, DuplicateClass.EXACT)
        self.assertTrue(evidence.exact_sha256)

    def test_same_normalized_content_does_not_become_exact_bytes(self):
        first = ["Page 1\nOSPF timers must match.", "2\nVLAN trunks carry allowed VLANs."]
        second = ["i\nOSPF   timers must match.", "Page 44\nVLAN trunks carry allowed VLANs."]
        evidence = classify("a" * 64, "b" * 64, first, second)
        self.assertEqual(evidence.classification, DuplicateClass.CONFIRMED_CONTENT)
        self.assertFalse(evidence.exact_sha256)

    def test_edition_change_is_not_a_deletion_decision(self):
        old = ["Title\nA", "Routing\nB", "Switching\nC"]
        new = ["Title\nA", "Routing changed\nD", "Wireless\nE", "Automation\nF"]
        evidence = classify("a" * 64, "b" * 64, old, new)
        self.assertIn(evidence.classification, {DuplicateClass.DIFFERENT_EDITION, DuplicateClass.INCONCLUSIVE})
        self.assertLess(evidence.confidence, 100)

    def test_normalization_removes_only_standalone_page_labels(self):
        self.assertEqual(normalize_page("Page 12\n  Router   ID 12 "), "router id 12")


if __name__ == "__main__":
    unittest.main()
