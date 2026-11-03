"""Controlled parser gate using only repository synthetic PDFs.

This does not install Docling and does not approve it. It records current extractor fallback
behaviour and a PDFium digital-text baseline for future Docling comparison.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.files.extract import EnvironmentProblem, Failure, extract


def run(fixtures: Path) -> dict:
    report: dict[str, object] = {"fixtures": {}, "docling": "not_installed_or_approved"}
    for path in sorted(fixtures.glob("*.pdf")):
        try:
            result = extract("pdf", path.read_bytes(), "eng")
        except EnvironmentProblem as error:
            report["fixtures"][path.name] = {"ok": False, "environment": str(error)}
            continue
        except Failure as error:
            report["fixtures"][path.name] = {"ok": False, "code": error.code}
            continue
        report["fixtures"][path.name] = {
            "ok": result.get("ok"),
            "code": result.get("code"),
            "pages": len(result.get("pages") or []),
            "ocrPages": result.get("ocrPages", 0),
            "characters": sum(len(page.get("text", "")) for page in result.get("pages") or []),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "knowledge" / "generated",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.fixtures), sort_keys=True))


if __name__ == "__main__":
    main()
