from __future__ import annotations

import hashlib
import io
import json
import unittest

from app.knowledge.protocol import ExtractionProtocolError, parse_ndjson


def _line(value):
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _valid(*, content="interface GigabitEthernet1/0/1"):
    digest = hashlib.sha256(content.encode() + b"\0").hexdigest()
    return b"".join(
        [
            _line(
                {
                    "kind": "header",
                    "protocol": "netsec-knowledge-extract",
                    "version": 2,
                    "document_sha256": "a" * 64,
                    "page_count": 3,
                    "parser": "synthetic/1",
                }
            ),
            _line(
                {
                    "kind": "element",
                    "sequence": 0,
                    "element_type": "code",
                    "content": content,
                    "physical_page": 2,
                    "printed_page_label": "1",
                    "section_path": ["Interface configuration"],
                    "bbox": [0, 0, 100, 50],
                    "metadata": {"language": "ios"},
                }
            ),
            _line({"kind": "footer", "elements": 1, "content_sha256": digest, "warnings": []}),
        ]
    )


class ProtocolTests(unittest.TestCase):
    def test_valid_protocol_preserves_page_provenance(self):
        parsed = parse_ndjson(io.BytesIO(_valid()))
        self.assertEqual(parsed.page_count, 3)
        self.assertEqual(parsed.elements[0]["physical_page"], 2)
        self.assertEqual(parsed.elements[0]["printed_page_label"], "1")

    def test_invalid_records_fail_closed(self):
        cases = [
            (lambda rows: [dict(rows[0], version=4), *rows[1:]], "unsupported_version"),
            (lambda rows: [dict(rows[0], surprise=True), *rows[1:]], "unknown_key"),
            (lambda rows: [dict(rows[0], document_metadata={"isbn13": []}), *rows[1:]], "invalid_document_metadata"),
            (
                lambda rows: [dict(rows[0], version=3, document_metadata={"t": "x" * 17_000}), *rows[1:]],
                "invalid_document_metadata",
            ),
            (lambda rows: [rows[0], dict(rows[1], physical_page=4), rows[2]], "invalid_page"),
            (lambda rows: [rows[0], dict(rows[1], sequence=2), rows[2]], "invalid_sequence"),
            (lambda rows: rows[:-1], "missing_header_or_footer"),
            (lambda rows: [*rows[:-1], dict(rows[-1], content_sha256="b" * 64)], "content_digest_mismatch"),
        ]
        for replace, code in cases:
            with self.subTest(code=code):
                rows = [json.loads(line) for line in _valid().splitlines()]
                payload = b"".join(_line(row) for row in replace(rows))
                with self.assertRaisesRegex(ExtractionProtocolError, code):
                    parse_ndjson(io.BytesIO(payload))

    def test_version_3_carries_the_book_metadata(self):
        rows = [json.loads(line) for line in _valid().splitlines()]
        rows[0] = dict(rows[0], version=3, document_metadata={"isbn13": ["9780135792735"], "edition_number": 2})
        parsed = parse_ndjson(io.BytesIO(b"".join(_line(row) for row in rows)))
        self.assertEqual(parsed.document_metadata["isbn13"], ["9780135792735"])

    def test_truncated_line_is_rejected(self):
        with self.assertRaisesRegex(ExtractionProtocolError, "line_too_large_or_truncated"):
            parse_ndjson(io.BytesIO(_valid()[:-1]))


if __name__ == "__main__":
    unittest.main()
