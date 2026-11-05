"""Read-only scan of book folders before any Knowledge Library import (for example the owner's CCNA and CCNP).

    python scripts/scan_knowledge_folder.py --root ccna=F:\\CCNA --root ccnp=F:\\CCNP --out C:\\scan\\books.json

Nothing is written beside the books, nothing is uploaded and no book text is printed. For each PDF: its SHA-256,
size, pages, scanned pages (OCR will be needed), outline entries, page labels and what its first pages say about
it (ISBN, edition, volume, exam codes, year); then every pair of PDFs that are copies, editions, volumes or
related, with the classes the import review uses (app/knowledge/passages.classify_pages); last, every file is
hashed again and the report says whether anything changed (it must not). PDFs are read in this process with
pypdfium2, as any viewer would: run it only on the owner's own books. Exit 0 when the scan is complete and no
file changed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge import extract  # noqa: E402
from app.knowledge.passages import classify_pages, page_keys, work_key  # noqa: E402
from scripts.import_windows_folder import FolderImportError, InventoryEntry, inventory_pdfs  # noqa: E402

METADATA_KEYS = ("title", "isbn13", "edition_statement", "edition_number", "volume", "exam_codes", "year")


def _read(entry: InventoryEntry) -> dict[str, Any]:
    """What one PDF is, without its text; pageKeys (ordered page identities) are dropped before the report."""
    record: dict[str, Any] = {"sizeBytes": entry.size_bytes, "sha256": entry.sha256}
    if entry.size_bytes > extract.MAX_INPUT_BYTES:
        return {**record, "status": "too_large", "pageKeys": []}
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    try:
        pdf = pdfium.PdfDocument(entry.path.read_bytes())
    except pdfium.PdfiumError as error:
        status = "encrypted" if getattr(error, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD else "corrupt"
        return {**record, "status": status, "pageKeys": []}
    try:
        count = len(pdf)
        if not 1 <= count <= extract.MAX_PAGES:
            return {**record, "status": "too_many_pages" if count else "corrupt", "pages": count, "pageKeys": []}
        texts, scanned = extract._scanned(pdf, pdfium_c, count)
        running = extract._running_lines(texts)
        cleaned = [extract._clean(text, running) for text in texts]
        metadata = extract._document_metadata(pdf, cleaned)
        labels = extract._labels(pdf, count)
        return {
            **record,
            "status": "ok",
            "pages": count,
            "scannedPages": len(scanned),
            "outlineEntries": len(extract._outline(pdf, count)),
            "pageLabels": any(label is not None for label in labels),
            "metadata": {key: metadata[key] for key in METADATA_KEYS if key in metadata},
            "workKey": work_key(str(metadata.get("title") or entry.path.stem)),
            "pageKeys": [key for _, key, _ in page_keys(list(enumerate(cleaned, start=1)))],
        }
    finally:
        pdf.close()


def _pairs(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for left, right in combinations(files, 2):
        classification, confidence, signals = classify_pages(
            left["pageKeys"], right["pageKeys"], left["sha256"] == right["sha256"]
        )
        if classification != "inconclusive":
            found.append(
                {"a": left["file"], "b": right["file"], "classification": str(classification), "confidence": confidence}
                | {key: value for key, value in signals.items() if key != "sha256"}
            )
    return found


def _listing(inventory: dict[str, list[InventoryEntry]]) -> dict[str, list[tuple[str, str, int]]]:
    return {label: [(e.relative_path, e.sha256, e.size_bytes) for e in entries] for label, entries in inventory.items()}


def scan(roots: dict[str, Path]) -> dict[str, Any]:
    before = {label: inventory_pdfs(root) for label, root in roots.items()}
    files = [
        {"file": f"{label}/{entry.relative_path}", **_read(entry)}
        for label, entries in before.items()
        for entry in entries
    ]
    after = {label: inventory_pdfs(root) for label, root in roots.items()}
    pairs = _pairs(files)
    for record in files:
        record.pop("pageKeys")
    return {
        "roots": {label: str(root) for label, root in roots.items()},
        "files": files,
        "pairs": pairs,
        "summary": {
            "pdfs": len(files),
            "bytes": sum(record["sizeBytes"] for record in files),
            "pages": sum(record.get("pages", 0) for record in files),
            "scannedPages": sum(record.get("scannedPages", 0) for record in files),
            "unreadable": sorted(record["file"] for record in files if record["status"] != "ok"),
            "duplicatePairs": len(pairs),
        },
        "unchanged": _listing(before) == _listing(after),
    }


def _roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        label, separator, path = value.partition("=")
        if not separator or not label.strip() or not path.strip() or label.strip() in roots:
            raise SystemExit("--root takes label=folder, each label once (for example ccna=F:\\CCNA).")
        roots[label.strip()] = Path(path.strip())
    return roots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", action="append", required=True, help="label=folder, repeatable")
    parser.add_argument("--out", help="where to write the JSON report (outside every scanned folder)")
    args = parser.parse_args(argv)
    roots = _roots(args.root)
    out = Path(args.out).resolve() if args.out else None
    if out is not None and any(out.is_relative_to(root.resolve()) for root in roots.values()):
        print("--out must be outside the scanned folders: nothing is written beside the books.", file=sys.stderr)
        return 2
    try:
        report = scan(roots)
    except FolderImportError as error:
        print(f"Scan stopped: {error}.", file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2, sort_keys=True)
    if out is not None:
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    summary = report["summary"]
    print(
        f"{summary['pdfs']} PDFs, {summary['pages']} pages, {summary['scannedPages']} scanned, "
        f"{summary['duplicatePairs']} related pairs, unchanged={str(report['unchanged']).lower()}",
        file=sys.stderr,
    )
    return 0 if report["unchanged"] and not summary["unreadable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
