"""Sandboxed PDFium/Tesseract adapter for Knowledge Extract NDJSON v2."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

from app.files.extract import EnvironmentProblem, Failure, _pdf, normalize
from app.knowledge.protocol import MAX_CONTENT_BYTES, PROTOCOL, VERSION

MAX_INPUT_BYTES = 50 * 1024 * 1024


def _write(record: dict[str, Any]) -> None:
    sys.stdout.buffer.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")


def records(data: bytes) -> list[dict[str, Any]]:
    document_sha = hashlib.sha256(data).hexdigest()
    pages, page_count, ocr_pages = _pdf(data, "eng")
    result: list[dict[str, Any]] = [
        {
            "kind": "header",
            "protocol": PROTOCOL,
            "version": VERSION,
            "document_sha256": document_sha,
            "page_count": page_count,
            "parser": "pdfium+tesseract/v2",
        }
    ]
    digest = hashlib.sha256()
    sequence = 0
    max_chars = MAX_CONTENT_BYTES // 4
    for page in pages:
        content = normalize(page["text"])
        parts = [content[index : index + max_chars] for index in range(0, len(content), max_chars)] or [""]
        for part in parts:
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
            result.append(
                {
                    "kind": "element",
                    "sequence": sequence,
                    "element_type": "paragraph",
                    "content": part,
                    "physical_page": int(page["meta"]["page"]),
                    "printed_page_label": None,
                    "section_path": [],
                    "bbox": None,
                    "metadata": {"ocrPages": ocr_pages},
                }
            )
            sequence += 1
    result.append(
        {
            "kind": "footer",
            "elements": sequence,
            "content_sha256": digest.hexdigest(),
            "warnings": ["ocr_used"] if ocr_pages else [],
        }
    )
    return result


def main() -> int:
    data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        return 2
    try:
        output = records(data)
    except (Failure, EnvironmentProblem):
        return 3
    for record in output:
        _write(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
