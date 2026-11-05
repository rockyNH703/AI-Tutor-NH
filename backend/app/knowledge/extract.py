"""Sandboxed PDFium/Tesseract book reader for Knowledge Extract NDJSON v3.

A book is read the way a reader finds their way in it: the PDF outline gives each page its chapter and section
(split where the heading appears on the page), the printed page labels are kept (iv, 17, A-3), running heads and
page numbers are removed, and every scanned page is read with OCR (in KNOWLEDGE_OCR_WORKERS processes) with its
confidence. Without an outline, numbered heading lines ("Chapter 5 ...", "5.2 ...") stand in for it. The header
carries what the book says about itself (PDF info, ISBN, edition, exam codes) for edition identity.

Runs only inside the systemd sandbox (no network, no secrets): stdin is the PDF, stdout is NDJSON v3.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from typing import Any

from app.files.extract import EnvironmentProblem, Failure, _ocr, normalize
from app.knowledge.protocol import MAX_CONTENT_BYTES, MAX_SECTION_DEPTH, PROTOCOL, VERSION

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_PAGES = 3_000
SCANNED_PAGE_TEXT = 200  # fewer characters than this on an image-covered page: a scanned page
SCANNED_PAGE_IMAGE_SHARE = 0.5
OCR_MAX_PIXELS = 12_000_000
OCR_MIN_CONFIDENCE = 60.0
OCR_LANGS = "eng"
MAX_OCR_WORKERS = 4
RUNNING_LINE_SHARE = 0.3  # a first or last line repeated on this share of pages is a running head or foot
METADATA_PAGES = 12  # where a book states its ISBN and edition

_PAGE_NUMBER = re.compile(r"^(?:page\s+)?(?:[ivxlcdm]+|\d{1,4})$", re.IGNORECASE)
_NUMBERED_HEADING = re.compile(
    r"^(?:chapter\s+\d{1,3}\b.*|appendix\s+[a-z]\b.*|\d{1,2}(?:\.\d{1,2}){0,2}\s+[A-Z].{2,90})$"
)
_ISBN = re.compile(r"ISBN(?:-1[03])?:?\s*((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dX])", re.IGNORECASE)
_EDITION = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|\d{1,2}(?:st|nd|rd|th))\s+edition\b", re.IGNORECASE
)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7}
_VOLUME = re.compile(r"\bvol(?:ume)?\.?\s*(\d{1,2}|[ivx]{1,4})\b", re.IGNORECASE)
_EXAM = re.compile(r"\b([1-5]\d{2}-\d{3})\b")
_YEAR = re.compile(r"(?:copyright|©)\s*(?:©\s*)?((?:19|20)\d{2})", re.IGNORECASE)


def _write(record: dict[str, Any]) -> None:
    sys.stdout.buffer.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")


# ----------------------------------------------------------------------------- OCR, in worker processes
_WORKER_PDF = None


def _init_ocr_worker(data: bytes) -> None:
    global _WORKER_PDF  # noqa: PLW0603 - one document per worker process
    import pypdfium2 as pdfium

    _WORKER_PDF = pdfium.PdfDocument(data)


def _ocr_page(index: int) -> tuple[int, str, float]:
    page = _WORKER_PDF[index]
    try:
        width, height = page.get_size()
        scale = min(2.5, math.sqrt(OCR_MAX_PIXELS / max(1.0, width * height)))
        image = page.render(scale=scale).to_pil().convert("L")
    finally:
        page.close()
    text, confidence = _ocr(image, OCR_LANGS)
    return index, text, confidence


def _ocr_workers() -> int:
    try:
        wanted = int(os.environ.get("KNOWLEDGE_OCR_WORKERS", "1"))
    except ValueError:
        wanted = 1
    return max(1, min(MAX_OCR_WORKERS, wanted))


# ----------------------------------------------------------------------------- structure
def _one_line(value: object, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _outline(pdf, page_count: int) -> list[tuple[int, tuple[str, ...]]]:
    """(page index, section path) for every outline entry that points inside the book, in page order."""
    entries: list[tuple[int, tuple[str, ...]]] = []
    path: list[str] = []
    try:
        toc = list(pdf.get_toc(max_depth=MAX_SECTION_DEPTH))
    except Exception:  # noqa: BLE001 - a damaged outline is no outline
        return []
    for item in toc:
        title = _one_line(getattr(item, "title", ""))
        level = int(getattr(item, "level", 0) or 0)
        index = getattr(item, "page_index", None)
        if not title or index is None or not 0 <= index < page_count:
            continue
        path = [*path[:level], title][:MAX_SECTION_DEPTH]
        entries.append((int(index), tuple(path)))
    return sorted(entries, key=lambda entry: entry[0])


def _labels(pdf, page_count: int) -> list[str | None]:
    labels: list[str | None] = []
    for index in range(page_count):
        try:
            label = pdf.get_page_label(index)
        except Exception:  # noqa: BLE001 - no label for this page
            label = None
        labels.append(label.strip()[:40] if isinstance(label, str) and label.strip() else None)
    return labels


def _running_lines(texts: list[str]) -> set[str]:
    """First and last lines repeated across many pages (running heads, feet, book titles)."""
    counts: Counter[str] = Counter()
    pages = 0
    for text in texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        pages += 1
        counts.update({lines[0].casefold(), lines[-1].casefold()})
    if pages < 5:
        return set()
    return {line for line, count in counts.items() if count >= RUNNING_LINE_SHARE * pages and len(line) < 120}


def _clean(text: str, running: set[str]) -> str:
    lines = [
        line
        for line in normalize(text).splitlines()
        if not _PAGE_NUMBER.fullmatch(line.strip()) and line.strip().casefold() not in running
    ]
    return "\n".join(lines).strip()


def _split_at_heading(text: str, title: str) -> tuple[str, str] | None:
    """The page text before and from the heading `title`, when the heading appears on the page."""
    words = title.split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    match = pattern.search(text)
    if match is None:
        return None
    return text[: match.start()].strip(), text[match.start() :].strip()


def _segments_without_outline(texts: list[str]):
    current: tuple[str, ...] = ()
    for index, text in enumerate(texts):
        parts: list[tuple[tuple[str, ...], list[str]]] = [(current, [])]
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) <= 100 and _NUMBERED_HEADING.fullmatch(stripped):
                chapter = stripped.lower().startswith(("chapter", "appendix"))
                current = (stripped,) if chapter else (*current[:1], stripped)
                parts.append((current, []))
            parts[-1][1].append(line)
        for path, lines in parts:
            body = "\n".join(lines).strip()
            if body:
                yield index, path, body


def _segments(texts: list[str], outline: list[tuple[int, tuple[str, ...]]]):
    """(page index, section path, text) in reading order."""
    if not outline:
        yield from _segments_without_outline(texts)
        return
    by_page: dict[int, list[tuple[str, ...]]] = {}
    for index, path in outline:
        by_page.setdefault(index, []).append(path)
    current: tuple[str, ...] = ()
    for index, text in enumerate(texts):
        remaining = text
        pieces: list[tuple[tuple[str, ...], str]] = []
        for path in by_page.get(index, []):
            split = _split_at_heading(remaining, path[-1])
            if split is not None:
                before, remaining = split
                pieces.append((current, before))
            current = path
        pieces.append((current, remaining))
        for path, body in pieces:
            if body.strip():
                yield index, path, body.strip()


# ----------------------------------------------------------------------------- what the book says about itself
def _isbn13(raw: str) -> str | None:
    """An ISBN-10 or ISBN-13 with a valid check digit, as ISBN-13."""
    digits = re.sub(r"[^0-9X]", "", raw.upper())
    if len(digits) == 10:
        if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(digits)) % 11:
            return None
        digits = "978" + digits[:9]
        return digits + str((10 - sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits)) % 10) % 10)
    if len(digits) == 13 and digits.isdigit():
        if sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(digits)) % 10 == 0:
            return digits
    return None


def _document_metadata(pdf, texts: list[str]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        raw = pdf.get_metadata_dict(skip_empty=True)
    except Exception:  # noqa: BLE001
        raw = {}
    for key in ("Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            info[key.lower()] = _one_line(raw[key])
    front = "\n".join(texts[:METADATA_PAGES])
    isbns = sorted({isbn for isbn in (_isbn13(match) for match in _ISBN.findall(front)) if isbn})[:8]
    if isbns:
        info["isbn13"] = isbns
    if edition := _EDITION.search(front):
        word = edition.group(1).lower()
        info["edition_statement"] = _one_line(edition.group(0), 120)
        info["edition_number"] = _ORDINALS.get(word) or int(re.match(r"\d+", word).group(0))
    if volume := _VOLUME.search(front[:4000]):
        info["volume"] = volume.group(1)[:40]
    exams = sorted(set(_EXAM.findall(front)))[:8]
    if exams:
        info["exam_codes"] = exams
    if year := _YEAR.search(front):
        info["year"] = int(year.group(1))
    return info


# ----------------------------------------------------------------------------- the reader
def _scanned(pdf, pdfium_c, count: int) -> tuple[list[str], list[int]]:
    texts: list[str] = []
    scanned: list[int] = []
    for index in range(count):
        page = pdf[index]
        textpage = page.get_textpage()
        texts.append(textpage.get_text_range())
        textpage.close()
        if len(texts[-1].strip()) < SCANNED_PAGE_TEXT:
            width, height = page.get_size()
            covered = 0.0
            for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,)):
                left, bottom, right, top = obj.get_bounds()
                covered += max(0.0, right - left) * max(0.0, top - bottom)
            if width * height > 0 and covered / (width * height) >= SCANNED_PAGE_IMAGE_SHARE:
                scanned.append(index)
        page.close()
    return texts, scanned


def read_book(data: bytes) -> tuple[list[dict[str, Any]], int, dict[str, Any], list[str]]:
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except ImportError as error:
        raise EnvironmentProblem("pypdfium2") from error
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise Failure(
            "encrypted" if getattr(error, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD else "corrupt"
        ) from error
    try:
        count = len(pdf)
        if count < 1:
            raise Failure("corrupt")
        if count > MAX_PAGES:
            raise Failure("too_many_pages")
        texts, scanned = _scanned(pdf, pdfium_c, count)
        warnings: list[str] = []
        if scanned:
            low_confidence = 0
            with concurrent.futures.ProcessPoolExecutor(
                max_workers=_ocr_workers(), initializer=_init_ocr_worker, initargs=(data,)
            ) as pool:
                for index, text, confidence in pool.map(_ocr_page, scanned):
                    if confidence >= OCR_MIN_CONFIDENCE:
                        texts[index] = f"{texts[index]}\n{text}"
                    else:
                        low_confidence += 1
            warnings.append("ocr_used")
            if low_confidence:
                warnings.append(f"ocr_low_confidence:{low_confidence}")
        outline = _outline(pdf, count)
        if not outline:
            warnings.append("no_outline")
        labels = _labels(pdf, count)
        running = _running_lines(texts)
        cleaned = [_clean(text, running) for text in texts]
        metadata = _document_metadata(pdf, cleaned)
        metadata["ocr_pages"] = len(scanned)
        elements = [
            {"page": index, "section": path, "label": labels[index], "text": body}
            for index, path, body in _segments(cleaned, outline)
        ]
        return elements, count, metadata, warnings
    finally:
        pdf.close()


def records(data: bytes) -> list[dict[str, Any]]:
    document_sha = hashlib.sha256(data).hexdigest()
    elements, page_count, metadata, warnings = read_book(data)
    result: list[dict[str, Any]] = [
        {
            "kind": "header",
            "protocol": PROTOCOL,
            "version": VERSION,
            "document_sha256": document_sha,
            "page_count": page_count,
            "parser": "pdfium+tesseract/v3",
            "document_metadata": metadata,
        }
    ]
    digest = hashlib.sha256()
    sequence = 0
    max_chars = MAX_CONTENT_BYTES // 4
    for element in elements:
        content = element["text"]
        for start in range(0, len(content), max_chars):
            part = content[start : start + max_chars]
            digest.update(part.encode("utf-8"))
            digest.update(b"\0")
            result.append(
                {
                    "kind": "element",
                    "sequence": sequence,
                    "element_type": "paragraph",
                    "content": part,
                    "physical_page": element["page"] + 1,
                    "printed_page_label": element["label"],
                    "section_path": list(element["section"]),
                    "bbox": None,
                    "metadata": {},
                }
            )
            sequence += 1
    result.append({"kind": "footer", "elements": sequence, "content_sha256": digest.hexdigest(), "warnings": warnings})
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
