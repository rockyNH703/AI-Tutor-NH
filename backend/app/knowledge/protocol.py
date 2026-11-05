"""Authoritative parsing of the bounded Knowledge Extract NDJSON v2 protocol."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, BinaryIO

PROTOCOL = "netsec-knowledge-extract"
VERSION = 3  # v3 (book reader): the header adds document_metadata; v2 outputs are still read
SUPPORTED_VERSIONS = (2, 3)
MAX_DOCUMENT_METADATA_BYTES = 16 * 1024
MAX_STDOUT_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_CONTENT_BYTES = 256 * 1024
MAX_METADATA_STRING_BYTES = 4 * 1024
MAX_ELEMENTS = 200_000
MAX_SECTION_DEPTH = 16
ALLOWED_KINDS = {"header", "element", "footer"}
ELEMENT_TYPES = {"heading", "paragraph", "list_item", "table", "figure", "code", "page_label"}
HEADER_KEYS = {"kind", "protocol", "version", "document_sha256", "page_count", "parser", "document_metadata"}
ELEMENT_KEYS = {
    "kind",
    "sequence",
    "element_type",
    "content",
    "physical_page",
    "printed_page_label",
    "section_path",
    "bbox",
    "metadata",
}
FOOTER_KEYS = {"kind", "elements", "content_sha256", "warnings"}


class ExtractionProtocolError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExtractedDocument:
    document_sha256: str
    page_count: int
    parser: str
    elements: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    content_sha256: str
    document_metadata: dict[str, Any] = field(default_factory=dict)  # v3: what the book says about itself


def _fail(code: str) -> None:
    raise ExtractionProtocolError(code)


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _bounded_strings(value: object) -> bool:
    if isinstance(value, str):
        return len(value.encode("utf-8")) <= MAX_METADATA_STRING_BYTES
    if isinstance(value, list):
        return all(_bounded_strings(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _bounded_strings(key) and _bounded_strings(item) for key, item in value.items()
        )
    return value is None or isinstance(value, (bool, int, float))


def _object(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("invalid_json")
    if not isinstance(value, dict):
        _fail("not_an_object")
    kind = value.get("kind")
    if kind not in ALLOWED_KINDS:
        _fail("unknown_kind")
    allowed = HEADER_KEYS if kind == "header" else ELEMENT_KEYS if kind == "element" else FOOTER_KEYS
    if set(value) - allowed:
        _fail("unknown_key")
    return value


def parse_ndjson(stream: BinaryIO) -> ExtractedDocument:
    total = 0
    records: list[dict[str, Any]] = []
    while True:
        line = stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            break
        total += len(line)
        if total > MAX_STDOUT_BYTES:
            _fail("stdout_too_large")
        if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
            _fail("line_too_large_or_truncated")
        records.append(_object(line))
    if len(records) < 2 or records[0].get("kind") != "header" or records[-1].get("kind") != "footer":
        _fail("missing_header_or_footer")
    header, footer = records[0], records[-1]
    elements = records[1:-1]
    if any(record.get("kind") != "element" for record in elements):
        _fail("record_order")
    if header.get("protocol") != PROTOCOL or header.get("version") not in SUPPORTED_VERSIONS:
        _fail("unsupported_version")
    document_metadata = header.get("document_metadata", {})
    if (
        ("document_metadata" in header and header.get("version") < 3)
        or not isinstance(document_metadata, dict)
        or not _bounded_strings(document_metadata)
        or len(json.dumps(document_metadata).encode("utf-8")) > MAX_DOCUMENT_METADATA_BYTES
    ):
        _fail("invalid_document_metadata")
    if not _sha(header.get("document_sha256")):
        _fail("invalid_document_digest")
    page_count = header.get("page_count")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        _fail("invalid_page_count")
    parser = header.get("parser")
    if not isinstance(parser, str) or not parser or len(parser) > 160:
        _fail("invalid_parser")
    if len(elements) > MAX_ELEMENTS or footer.get("elements") != len(elements):
        _fail("invalid_element_count")
    content_digest = hashlib.sha256()
    for expected, element in enumerate(elements):
        if element.get("sequence") != expected:
            _fail("invalid_sequence")
        if element.get("element_type") not in ELEMENT_TYPES:
            _fail("unknown_element_type")
        content = element.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            _fail("invalid_content")
        page = element.get("physical_page")
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= page_count:
            _fail("invalid_page")
        section = element.get("section_path", [])
        if (
            not isinstance(section, list)
            or len(section) > MAX_SECTION_DEPTH
            or not all(isinstance(x, str) for x in section)
        ):
            _fail("invalid_section_path")
        bbox = element.get("bbox")
        if bbox is not None and (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in bbox)
            or bbox[0] > bbox[2]
            or bbox[1] > bbox[3]
        ):
            _fail("invalid_bbox")
        metadata = element.get("metadata", {})
        if not isinstance(metadata, dict) or not _bounded_strings(metadata):
            _fail("invalid_metadata")
        label = element.get("printed_page_label")
        if label is not None and (not isinstance(label, str) or len(label.encode("utf-8")) > 80):
            _fail("invalid_printed_page_label")
        content_digest.update(content.encode("utf-8"))
        content_digest.update(b"\0")
    footer_digest = footer.get("content_sha256")
    if not _sha(footer_digest) or footer_digest != content_digest.hexdigest():
        _fail("content_digest_mismatch")
    warnings = footer.get("warnings")
    if not isinstance(warnings, list) or not all(
        isinstance(warning, str) and len(warning) <= 200 for warning in warnings
    ):
        _fail("invalid_warnings")
    return ExtractedDocument(
        document_sha256=header["document_sha256"],
        page_count=page_count,
        parser=parser,
        elements=tuple(elements),
        warnings=tuple(warnings),
        content_sha256=footer_digest,
        document_metadata=dict(document_metadata),
    )
