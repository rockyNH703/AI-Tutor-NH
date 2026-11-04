from __future__ import annotations

import hashlib
import io
import json
import unittest
from pathlib import Path

from app.knowledge.extract import records
from app.knowledge.protocol import parse_ndjson


class KnowledgeExtractorAdapterTests(unittest.TestCase):
    def test_repository_fixture_round_trips(self):
        path = Path(__file__).parent / "fixtures" / "knowledge" / "generated" / "digital-structure.pdf"
        data = path.read_bytes()
        output = records(data)
        raw = b"".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n" for record in output
        )

        extracted = parse_ndjson(io.BytesIO(raw))

        self.assertEqual(extracted.document_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(extracted.page_count, 2)
        self.assertGreater(len(extracted.elements), 0)


if __name__ == "__main__":
    unittest.main()
