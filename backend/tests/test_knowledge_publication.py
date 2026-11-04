from __future__ import annotations

import hashlib
import unittest

from app.knowledge.protocol import ExtractedDocument
from app.knowledge.publication import MAX_CHUNK_CHARS, build_chunks


class PublicationTests(unittest.TestCase):
    def test_structure_and_page_provenance_make_stable_keys(self):
        content = "router ospf 10\n" + ("network 192.0.2.0 area 0 " * 150)
        extracted = ExtractedDocument(
            document_sha256="a" * 64,
            page_count=2,
            parser="synthetic/1",
            elements=(
                {
                    "sequence": 0,
                    "element_type": "code",
                    "content": content,
                    "physical_page": 2,
                    "printed_page_label": "1",
                    "section_path": ["Routing", "OSPF"],
                    "metadata": {"language": "ios"},
                },
            ),
            warnings=(),
            content_sha256=hashlib.sha256(content.encode() + b"\0").hexdigest(),
        )
        first = build_chunks(extracted)
        second = build_chunks(extracted)
        self.assertEqual(first, second)
        self.assertGreater(len(first), 1)
        self.assertTrue(all(len(chunk.content) <= MAX_CHUNK_CHARS for chunk in first))
        self.assertTrue(all(chunk.physical_page_start == 2 for chunk in first))
        self.assertTrue(all(chunk.printed_page_label == "1" for chunk in first))
        self.assertEqual(len({chunk.stable_key for chunk in first}), len(first))


if __name__ == "__main__":
    unittest.main()
