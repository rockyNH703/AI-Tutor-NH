"""The read-only folder scanner on synthetic PDFs (CC0 text built by tests/fixtures/knowledge/generate.py)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from scripts import scan_knowledge_folder

_SPEC = importlib.util.spec_from_file_location(
    "knowledge_fixture_generator", Path(__file__).parent / "fixtures" / "knowledge" / "generate.py"
)
_GENERATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GENERATOR)
SECRET_LINE = "Synthetic OSPF hello timers decide adjacency on every broadcast segment"


def _page(n: int) -> list[tuple[int, int, int, str]]:
    return [
        (54, 744, 14, f"Chapter {n}: Synthetic Routing Topic {n}"),
        (54, 700, 10, f"{SECRET_LINE} number {n}."),
        (54, 680, 10, f"Area {n} routers exchange link-state advertisements and agree on timers."),
        (54, 660, 10, f"Priority {n} breaks the tie when two routers want to be the designated router."),
    ]


def _book(path: Path, pages: range, *, front: bool = True) -> bytes:
    first = [(54, 600, 10, "ISBN 978-0-13-579273-5 Second Edition Volume 1 Exam 200-301")] if front else []
    data = _GENERATOR._digital_pdf([_page(n) + (first if n == pages[0] else []) for n in pages])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _folders(tmp_path: Path, *, corrupt: bool) -> tuple[Path, Path]:
    ccna, ccnp = tmp_path / "CCNA", tmp_path / "CCNP"
    data = _book(ccna / "guide.pdf", range(1, 6))
    (ccnp / "renamed copy.pdf").parent.mkdir(parents=True, exist_ok=True)
    (ccnp / "renamed copy.pdf").write_bytes(data)
    _book(ccnp / "combined.pdf", range(10, 22), front=False)
    _book(ccnp / "volume.pdf", range(10, 16), front=False)
    if corrupt:
        (ccna / "broken.pdf").write_bytes(b"%PDF-1.4\nnot a real pdf\n")
    return ccna, ccnp


def test_the_scan_finds_copies_volumes_and_metadata_without_text_or_changes(tmp_path):
    ccna, ccnp = _folders(tmp_path, corrupt=True)

    report = scan_knowledge_folder.scan({"ccna": ccna, "ccnp": ccnp})

    assert report["unchanged"] is True
    assert report["summary"]["pdfs"] == 5 and report["summary"]["unreadable"] == ["ccna/broken.pdf"]
    files = {record["file"]: record for record in report["files"]}
    guide = files["ccna/guide.pdf"]
    assert (guide["status"], guide["pages"], guide["scannedPages"]) == ("ok", 5, 0)
    assert guide["metadata"]["isbn13"] == ["9780135792735"] and guide["metadata"]["edition_number"] == 2
    assert guide["metadata"]["exam_codes"] == ["200-301"]
    pairs = {(pair["a"], pair["b"]): pair["classification"] for pair in report["pairs"]}
    assert pairs[("ccna/guide.pdf", "ccnp/renamed copy.pdf")] == "exact"
    assert pairs[("ccnp/combined.pdf", "ccnp/volume.pdf")] == "superset"
    assert SECRET_LINE not in json.dumps(report)  # identities and counts only, never book text


def test_the_command_refuses_to_write_beside_the_books_and_fails_on_an_unreadable_pdf(tmp_path, capsys):
    ccna, ccnp = _folders(tmp_path, corrupt=True)
    roots = ["--root", f"ccna={ccna}", "--root", f"ccnp={ccnp}"]

    assert scan_knowledge_folder.main([*roots, "--out", str(ccna / "scan.json")]) == 2
    assert not (ccna / "scan.json").exists()
    assert scan_knowledge_folder.main([*roots, "--out", str(tmp_path / "scan.json")]) == 1  # broken.pdf
    assert json.loads((tmp_path / "scan.json").read_text(encoding="utf-8"))["unchanged"] is True


def test_a_clean_scan_exits_zero(tmp_path, capsys):
    ccna, ccnp = _folders(tmp_path, corrupt=False)

    assert scan_knowledge_folder.main(["--root", f"ccna={ccna}", "--root", f"ccnp={ccnp}"]) == 0
    assert "unchanged=true" in capsys.readouterr().err
