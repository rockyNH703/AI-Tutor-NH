"""Sandboxed PDFium/Tesseract book reader for Knowledge Extract NDJSON v3.

A book is read the way a reader finds their way in it: the PDF outline gives each page its chapter and section
(split where the heading appears on the page), the printed page labels are kept (iv, 17, A-3), running heads and
page numbers are removed, and every scanned page is read with OCR (in KNOWLEDGE_OCR_WORKERS processes) with its
confidence. Without an outline, the chapter a running head names ("Chapter 22: ... 573"), chapter lines and dotted
topic numbers ("1.0 ...", "1.1 ...") stand in for it. Contents and back-index pages only point into the book and
are not emitted (warning navigation_pages:N). The header carries what the book says about itself (PDF info, ISBN,
edition, exam codes) for edition identity.

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

MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_PAGES = 4_500  # the largest real book (FortiOS 7.6 Admin Guide, 4,169 pages) fits; stays below passages.KEY_BATCH
SCANNED_PAGE_TEXT = 200  # fewer characters than this on an image-covered page: a scanned page
SCANNED_PAGE_IMAGE_SHARE = 0.5
OCR_MAX_PIXELS = 12_000_000
OCR_MIN_CONFIDENCE = 60.0
OCR_LANGS = "eng"
MAX_OCR_WORKERS = 4
RUNNING_LINE_SHARE = 0.3  # a first or last line repeated on this share of pages is a running head or foot
METADATA_PAGES = 12  # where a book states its ISBN and edition

_PAGE_NUMBER = re.compile(r"^(?:page\s+)?(?:[ivxlcdm]+|\d{1,4})$", re.IGNORECASE)
_ROMAN = re.compile(r"[ivxlcdm]+", re.IGNORECASE)
# Without an outline a book's chapters come from its running heads, a "Chapter N: Title" first line or a chapter
# opener ("CHAPTER 23" alone on a page's first or last line); its sections from dotted topic numbers (1.0, 1.1, 1.1.2).
_CHAPTER = re.compile(r"(?:chapter\s+(\d{1,3})|appendix\s+([a-z]))(?:\s*[:.–—-])?\s+(\S.*)", re.IGNORECASE)
_BARE_CHAPTER = re.compile(r"(?:chapter\s+(\d{1,3})|appendix\s+([a-z]))", re.IGNORECASE)
TITLE_CASE_SHARE = 0.6  # "Implementing Basic OSPF Features" is a title; "This new chapter contains ..." is a sentence
MAX_TITLE_WORDS = 10
_TOPIC = re.compile(r"(\d{1,2}(?:\.\d{1,2}){1,2})\s+([A-Z].{2,90})")
_WEIGHT = re.compile(r"^\d{1,3}\s?%\s+|\s+\d{1,3}\s?%$")  # an exam domain's weight: "25% 1.0 ...", "1.0 ... 20%"
_MEASURE = re.compile(r"(?:[kMGT]?Hz|[kKMGT]?bps|[kKMGT]?B|ms|dBm?|mW|W)\b")  # 2.4 GHz is not a heading
_WORD = re.compile(r"[A-Za-z]{3,}")
MAX_HEADING_CHARS = 100
MAX_HEAD_CHARS = 120  # a running head is short; a longer first or last line is text (and meets no regex)
_SUBTOPIC = re.compile(r"\s*\d{1,2}(?:\.\d{1,2}){1,2}\.?[a-z]\b")  # "1.13.a MAC learning": a list goes on
# Navigation pages (a book's contents and back index): their lines end in page numbers.
_INDEX_ENTRY = re.compile(r",\s+(\d{1,4})(?:\s*[-–]\s*\d{1,4})?\s*$")  # "DR election, 456" / "areas, 551-553"
_CONTENTS_ENTRY = re.compile(r"(?:(?<=[A-Za-z?):'’])\s+|\s*\.{2,}\s*|(?:\s\.){2,}\s*)(\d{1,4})\s*$")  # "WANs 62"
MAX_ENTRY_CHARS = 200
NAVIGATION_MIN_LINES = 8
INDEX_ENTRY_SHARE = 0.3  # measured on the CCNA/CCNP books: index pages 0.42-0.90, every other page below 0.2
INDEX_MIN_LINES = 40  # two-column index pages: 49-80 lines; a shorter page counts only right after an index page
BACK_MATTER_SHARE = 0.5  # the index is in the back half (from 64% of the pages in the partial CCNA sample)
CONTENTS_ENTRY_SHARE = 0.5  # contents pages 0.59-1.00, other pages at most 0.37 ...
CONTENTS_RISING_SHARE = 0.9  # ... and contents page numbers rise down the page (config listings do not)
FRONT_MATTER_SHARE = 0.15  # contents pages are front matter: a roman page label, or the first pages of the PDF
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


def _title_case(text: str) -> bool:
    """Most of its longer words capitalized: a heading or a book's title, not a sentence."""
    words = [word for word in re.findall(r"[A-Za-z][\w-]*", text) if len(word) >= 4]
    return bool(words) and sum(word[0].isupper() for word in words) >= TITLE_CASE_SHARE * len(words)


def _running_head(line: str, label: str | None) -> str | None:
    """The title of a running head or foot that carries the page's printed number at either end
    (568 CCNA 200-301 Official Cert Guide, Chapter 22: Implementing Basic OSPF Features 573), else None. A line of
    text that happens to end in the page number ("... on TCP port 443" on page 443) is not one: no title case."""
    line = line.strip()
    if not label or len(line) > MAX_HEAD_CHARS:
        return None
    number = re.escape(label.strip())
    match = re.fullmatch(rf"{number}\W+(.+)|(.+?)\W+{number}", line, re.IGNORECASE)
    if match is None:
        return None
    title = (match.group(1) or match.group(2)).strip()
    if len(re.findall(r"[A-Za-z]{2,}", title)) < 2 or not _title_case(title):  # "CHAPTER 1" opens a chapter
        return None
    return title


def _strip_page(text: str, running: set[str], label: str | None) -> tuple[str, str | None]:
    """The page text without page numbers, running lines and the head or foot that carries the page number, and
    the chapter that head names, if it names one."""
    lines = [
        line
        for line in normalize(text).splitlines()
        if not _PAGE_NUMBER.fullmatch(line.strip()) and line.strip().casefold() not in running
    ]
    filled = [index for index, line in enumerate(lines) if line.strip()]
    heads: set[int] = set()
    chapter = None
    for index in dict.fromkeys(filled[:1] + filled[-1:]):
        title = _running_head(lines[index], label)
        if title is not None:
            heads.add(index)
            if chapter is None and (named := _chapter_title(title, strict=False)):
                chapter = _one_line(named, MAX_HEADING_CHARS)
    return "\n".join(line for index, line in enumerate(lines) if index not in heads).strip(), chapter


def _clean(text: str, running: set[str], label: str | None = None) -> str:
    return _strip_page(text, running, label)[0]


def _page_numbers(lines: list[str], pattern: re.Pattern[str], last: int) -> list[int]:
    """The page numbers that end these lines (entries longer than MAX_ENTRY_CHARS are text), at most `last`."""
    found = (pattern.search(line) for line in lines if len(line) <= MAX_ENTRY_CHARS)
    return [number for match in found if match and (number := int(match.group(1))) <= last]


def _navigation(texts: list[str], labels: list[str | None]) -> set[int]:
    """Pages that only point into the book: its contents (front matter, page numbers rising down the page) and its
    back index ("DR election, 456"). A reply would cite a list of page numbers, so they are not searched. Tables of
    ports, distances or dates in the body stay: they are neither front matter nor index-shaped back matter, and
    numbers above the book's last printed page (2020, 1812) are no page references."""
    last = max((int(label) for label in labels if label and label.isdigit()), default=len(texts))
    pages: set[int] = set()
    for index, text in enumerate(texts):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < NAVIGATION_MIN_LINES:
            continue
        index_shaped = len(lines) >= INDEX_MIN_LINES or index - 1 in pages
        if index >= BACK_MATTER_SHARE * len(texts) and index_shaped:
            if len(_page_numbers(lines, _INDEX_ENTRY, last)) >= INDEX_ENTRY_SHARE * len(lines):
                pages.add(index)
                continue
        label = labels[index]
        if not (label and _ROMAN.fullmatch(label)) and index >= FRONT_MATTER_SHARE * len(texts):
            continue
        numbers = _page_numbers(lines, _CONTENTS_ENTRY, last)
        rising = sum(later >= earlier for earlier, later in zip(numbers, numbers[1:], strict=False))
        if len(numbers) >= CONTENTS_ENTRY_SHARE * len(lines) and rising >= CONTENTS_RISING_SHARE * (len(numbers) - 1):
            pages.add(index)
    return pages


def _ranges(pages: set[int]) -> str:
    """Physical page numbers as ranges, "11-33 79-123", for the operator (bounded: the protocol's warning limit)."""
    runs: list[list[int]] = []
    for page in sorted(pages):
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    text = " ".join(f"{start + 1}-{end + 1}" if end > start else f"{start + 1}" for start, end in runs)
    return text if len(text) <= 150 else text[:149] + "…"


def _pages(texts: list[str], labels: list[str | None]) -> tuple[list[str], list[str | None], set[int]]:
    """Each page's clean text, the chapter its running head names, and the navigation pages."""
    running = _running_lines(texts)
    stripped = [_strip_page(text, running, label) for text, label in zip(texts, labels, strict=True)]
    cleaned = [text for text, _ in stripped]
    return cleaned, [chapter for _, chapter in stripped], _navigation(cleaned, labels)


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


def _chapter_title(line: str, strict: bool = True) -> str | None:
    """ "Chapter 22: Implementing Basic OSPF Features" (or an appendix), without a trailing page number. Strict for a
    line of the page itself: a title (title case, at most MAX_TITLE_WORDS words), not a sentence or a list item
    ("Chapter 28: This new chapter contains ..."). A running head is a title by where it stands."""
    line = line.strip()
    if len(line) > MAX_HEAD_CHARS:
        return None
    match = _CHAPTER.fullmatch(_CONTENTS_ENTRY.sub("", line))
    if match is None:
        return None
    title = match.group(3)
    if strict and (title.startswith("(") or len(title.split()) > MAX_TITLE_WORDS or not _title_case(title)):
        return None
    return f"Chapter {match.group(1)}: {title}" if match.group(1) else f"Appendix {match.group(2).upper()}: {title}"


def _order(chapter: str) -> tuple[int, int]:
    """Where a chapter stands in a book: chapters by number, then appendices by letter."""
    match = _BARE_CHAPTER.match(chapter)
    if match is None:
        return (-1, 0)
    return (0, int(match.group(1))) if match.group(1) else (1, ord(match.group(2).lower()) - ord("a") + 1)


def _page_chapter(lines: list[str], hint: str | None, current: str | None) -> str | None:
    """The chapter a page names, if any: its running head's, a "Chapter N: Title" first line, or a chapter opener,
    "CHAPTER 23" alone on its first or last line (titled by the next line), later in the book than the current
    chapter. A label in a figure or an answer key's "Chapter 2" inside an appendix moves nothing."""
    if hint:
        return hint
    filled = [line.strip() for line in lines if line.strip()]
    if not filled:
        return None
    if titled := _chapter_title(filled[0]):
        return titled
    for position in dict.fromkeys((0, len(filled) - 1)):
        bare = _BARE_CHAPTER.fullmatch(filled[position])
        if bare is None or (current is not None and _order(filled[position]) <= _order(current)):
            continue
        following = filled[position + 1] if position == 0 and len(filled) > 1 else ""
        named = _chapter_title(f"{filled[position]}: {following}") if following else None
        return named or (f"Chapter {bare.group(1)}" if bare.group(1) else f"Appendix {bare.group(2).upper()}")
    return None


def _topic(line: str) -> tuple[tuple[str, ...], str] | None:
    """(number, title) of a dotted topic heading: "25% 1.0 Network Infrastructure and Connectivity" (an exam
    domain), "1.1 Explain ...", "3.2 Configuring OSPF". Answer keys (1 B 2 A), lines ending in a page number and
    measurements (2.4 GHz ...) are body text."""
    line = line.strip()
    if len(line) > MAX_HEADING_CHARS:
        return None
    match = _TOPIC.fullmatch(_WEIGHT.sub("", line))
    if match is None or _MEASURE.match(match.group(2)) or _CONTENTS_ENTRY.search(line):
        return None
    if not _WORD.search(match.group(2)):
        return None
    return tuple(match.group(1).split(".")), f"{match.group(1)} {match.group(2)}"


def _nest(topics: tuple[str, ...], number: tuple[str, ...]) -> tuple[str, ...]:
    """The open topics a new topic number sits under: its parent (1.0 or 1 for 1.1, 1.1 for 1.1.2) and the parent's
    own. A domain (1.0) or a topic whose parent never appeared starts again at the top."""
    if number[-1] == "0":
        return ()
    parents = {number[:-1], (*number[:-1], "0")}
    for cut in range(len(topics), 0, -1):
        found = _topic(topics[cut - 1])
        if found is not None and found[0] in parents:
            return topics[:cut]
    return ()


def _segments_without_outline(texts: list[str], chapters: list[str | None]):
    chapter: tuple[str, ...] = ()
    current: tuple[str, ...] = ()
    for index, text in enumerate(texts):
        lines = text.splitlines()
        topics = [_topic(line) for line in lines]
        named = _page_chapter(lines, chapters[index], chapter[0] if chapter else None)
        if named and chapter != (named,):
            chapter = current = (named,)
        elif not any(topics) and not any(_SUBTOPIC.match(line) for line in lines):
            # A topic number runs on only while its numbered list does: the exam topics a chapter opener or an
            # introduction lists are not the section of the pages after them. (A numbered section of a book with no
            # outline therefore labels its own page only: a missing label over a wrong one.)
            current = chapter
        parts: list[tuple[tuple[str, ...], list[str]]] = [(current, [])]
        for line, topic in zip(lines, topics, strict=True):
            if topic is not None:
                number, title = topic
                current = (*chapter, *_nest(current[len(chapter) :], number), title)[:MAX_SECTION_DEPTH]
                parts.append((current, []))
            parts[-1][1].append(line)
        for path, lines in parts:
            body = "\n".join(lines).strip()
            if body:
                yield index, path, body


def _segments(texts: list[str], outline: list[tuple[int, tuple[str, ...]]], chapters: list[str | None] | None = None):
    """(page index, section path, text) in reading order."""
    if not outline:
        yield from _segments_without_outline(texts, chapters or [None] * len(texts))
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
            has_text_objects = False
            for obj in page.get_objects():
                if obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                    has_text_objects = True
                elif obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                    left, bottom, right, top = obj.get_bounds()
                    covered += max(0.0, right - left) * max(0.0, top - bottom)
            # A page with no text objects is a picture of text however it is drawn: one big scan or many
            # tiles (tiled-image PDFs cover far less than the share threshold with each tile's bounds).
            if width * height > 0 and (not has_text_objects or covered / (width * height) >= SCANNED_PAGE_IMAGE_SHARE):
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
        labels = _labels(pdf, count)
        elements, cleaned, notes = _book_elements(texts, labels, outline)
        warnings.extend(notes)
        metadata = _document_metadata(pdf, cleaned)
        metadata["ocr_pages"] = len(scanned)
        return elements, count, metadata, warnings
    finally:
        pdf.close()


def _book_elements(
    texts: list[str], labels: list[str | None], outline: list[tuple[int, tuple[str, ...]]]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The book's elements (page, section, label, text), its clean page texts (what it says about itself is read
    from them) and the warnings its structure raises. Navigation pages are named in the warning by page."""
    cleaned, chapters, navigation = _pages(texts, labels)
    warnings = [] if outline else ["no_outline"]
    if navigation:
        warnings.append(f"navigation_pages:{len(navigation)} ({_ranges(navigation)})")
    searched = ["" if index in navigation else text for index, text in enumerate(cleaned)]
    elements = [
        {"page": index, "section": path, "label": labels[index], "text": body}
        for index, path, body in _segments(searched, outline, chapters)
    ]
    return elements, cleaned, warnings


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
            "parser": "pdfium+tesseract/v4",
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
