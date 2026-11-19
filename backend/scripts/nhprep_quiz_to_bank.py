"""NHprep's quiz questions (session 3's export v1 bundle) into the bank's files under qbank/content.

    python scripts/nhprep_quiz_to_bank.py <export run folder> ../qbank            # dry run on a copy
    python scripts/nhprep_quiz_to_bank.py <export run folder> ../qbank --write    # rewrite qbank/content

The export folder holds export.jsonl and manifest.json (C:\\Users\\test\\nhprep-data\\export\\<run>); it is only
read. Each category needs qbank/nhprep/quiz-map/<category>.json (app/qbank/nhprep_quiz.py). Prints counts and
question ids, never question text. Exit 1 when a question is unmapped or any file check fails. No database: import
with scripts/import_questions.py afterwards.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.qbank.nhprep_quiz import ExportError, convert  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path)
    parser.add_argument("root", type=Path)
    parser.add_argument("--write", action="store_true", help="rewrite the content files (default: a dry run)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        root = args.root
        if not args.write:
            root = Path(scratch) / "qbank"
            shutil.copytree(args.root, root)
        try:
            report = convert(args.export, root)
        except ExportError as error:
            print(f"REFUSED {error}")
            return 1
    for name, seen in sorted(report.coverage.items()):
        print(f"coverage {name}: seen {seen['seen']} of {seen['reported']} in {seen['attempts']} attempts")
    for qid in report.unmapped:
        print(f"UNMAPPED {qid}")
    for note in report.duplicates:
        print(f"DUPLICATE {note}")
    for error in report.errors:
        print(f"ERROR   {error}")
    print(
        f"exported {report.exported}, written {report.written}, retired {report.retired}, "
        f"unmapped {len(report.unmapped)}, duplicates {len(report.duplicates)}, errors {len(report.errors)}"
        + ("" if args.write else " (dry run: nothing written)")
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
