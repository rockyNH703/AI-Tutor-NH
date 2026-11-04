from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

GENERATOR = Path(__file__).parent / "fixtures" / "knowledge" / "generate.py"


class FixtureTests(unittest.TestCase):
    def test_generated_manifest_matches_original_pdf_bytes(self):
        spec = importlib.util.spec_from_file_location("knowledge_fixture_generator", GENERATOR)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = module.generate(root)
            persisted = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(generated, persisted)
            self.assertEqual(persisted["license"], "CC0-1.0")
            self.assertEqual(
                set(persisted["fixtures"]),
                {"digital-structure.pdf", "edition-a.pdf", "edition-b.pdf", "scanned-ocr.pdf"},
            )
            for name, entry in persisted["fixtures"].items():
                data = (root / name).read_bytes()
                self.assertTrue(data.startswith(b"%PDF-"))
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                self.assertGreaterEqual(entry["pages"], 1)
            self.assertNotEqual(
                persisted["fixtures"]["edition-a.pdf"]["sha256"],
                persisted["fixtures"]["edition-b.pdf"]["sha256"],
            )


if __name__ == "__main__":
    unittest.main()
