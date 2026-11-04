from __future__ import annotations

import unittest

from langchain_core.documents import Document

from app.chat.service import public_citations
from app.rag.context import source_entry


class CitationTests(unittest.TestCase):
    def test_revision_metadata_survives_public_projection(self):
        source = source_entry(
            Document(
                page_content="OSPF neighbors must agree on area, timers, and authentication.",
                metadata={
                    "chunk_id": "chunk",
                    "document_id": "document",
                    "source_id": "source",
                    "source": "ccna",
                    "title": "Synthetic routing guide",
                    "uri": "must-not-leak.pdf",
                    "ordinal": 4,
                    "score": 0.9,
                    "attributes": {
                        "revision_id": "revision",
                        "stable_chunk_key": "stable",
                        "section_path": ["Routing", "OSPF"],
                        "physical_page": 12,
                        "printed_page_label": "9",
                    },
                },
            )
        )
        citation = public_citations({"sources": [source]})[0]
        self.assertEqual(
            citation,
            {
                "ref": "1",
                "kind": "source",
                "title": "Synthetic routing guide",
                "documentId": "document",
                "revisionId": "revision",
                "stableChunkKey": "stable",
                "sectionPath": ["Routing", "OSPF"],
                "physicalPage": 12,
                "printedPageLabel": "9",
                "excerpt": "OSPF neighbors must agree on area, timers, and authentication.",
            },
        )
        self.assertNotIn("uri", citation)
        self.assertNotIn("score", citation)


if __name__ == "__main__":
    unittest.main()
