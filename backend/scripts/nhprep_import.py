"""Import an NHprep export v1 bundle into the nhprep_* tables, or only check it (plan rev 4 §8.2).

    python scripts/nhprep_import.py <run folder | export.jsonl | ->        # dry run: no writes, no provider calls
        [--sha256 HEX]                       # required for a stream ("-"), which is never complete (no removals)
        [--apply] [--operator NAME (--backup <dump> | --backup-waiver "<reason>")]
        [--access-basis "..."] [--egress-basis "..."]   # egress: required when any text will be embedded
        [--vectors vectors.jsonl]            # precomputed vectors (nhprep_embed_offline.py): the only embedder
        [--emit-texts texts.jsonl]           # dry run only: every distinct search text, for the offline embedder
        [--allow-removals N]                 # a complete export removing a whole course or > 5% of lessons
    python scripts/nhprep_import.py --purge --reason "<why>" [--apply] [--operator NAME ...]

The folder holds export.jsonl and manifest.json; the bytes must match the manifest's sha256 (and --sha256 if given).
Quiz records are skipped (the question bank's converter reads them). Prints counts and codes, never NHprep text.
Exit codes: 0 done, 1 refused or invalid, 3 another import or purge holds the lock. Embeddings come only from
build_embeddings (secrets masked) or --vectors; EMBEDDING_PROVIDER=fake is refused unless the database is *_test.
Uses DATABASE_URL from the environment / .env; never point it at production without the owner's authorisation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OPERATOR = re.compile(r"[a-z0-9][a-z0-9_.-]{0,79}")
SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _source(src: str) -> tuple[bytes, dict[str, Any] | None]:
    if src == "-":
        return sys.stdin.buffer.read(), None
    path = Path(src)
    export = path / "export.jsonl" if path.is_dir() else path
    manifest = export.with_name("manifest.json")
    return export.read_bytes(), json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None


def _check_args(args: argparse.Namespace) -> str | None:
    if not OPERATOR.fullmatch(args.operator or "local"):
        return "--operator must match [a-z0-9][a-z0-9_.-]{0,79}"
    if args.operator and args.apply and not (args.backup or args.backup_waiver):
        return "--operator --apply needs --backup <dump> or --backup-waiver <reason>"
    if args.backup and not (Path(args.backup).is_file() and Path(args.backup).stat().st_size > 0):
        return "--backup must name an existing, non-empty dump"
    if args.purge:
        return None if args.reason else "--purge needs --reason"
    if not args.src:
        return "give the export (a run folder, export.jsonl, or - for stdin)"
    if args.sha256 and not SHA256.fullmatch(args.sha256):
        return "--sha256 must be 64 hex characters"
    if args.emit_texts and args.apply:
        return "--emit-texts is for a dry run"
    return None


async def run(args: argparse.Namespace, export: Any, vectors: Any, emit: TextIO | None) -> int:
    from app.db.session import Database
    from app.nhprep.importer import ImportBusy, ImportOptions, ImportRefused, purge, run_import
    from app.rag.embeddings import build_embeddings
    from app.settings import get_settings

    settings = get_settings()
    db = Database(settings)
    database = db.engine.url.database or ""
    backup = f"backup:{Path(args.backup).name}" if args.backup else (args.backup_waiver and "waiver")
    operator = args.operator or "local"
    try:
        if args.purge:
            counts = await purge(db, operator=operator, reason=args.reason, apply=args.apply)
            print(json.dumps({"purged" if args.apply else "would_purge": counts, "backup": backup}, indent=1))
            return 0
        if vectors is not None:
            embedder = vectors
        elif settings.embedding_provider == "fake" and not database.endswith("_test"):
            print("REFUSED fake_provider: EMBEDDING_PROVIDER=fake outside a *_test database")
            return 1
        else:
            embedder = build_embeddings(settings)  # masked: secrets never reach the provider
        options = ImportOptions(
            apply=args.apply,
            operator=operator,
            access_basis=args.access_basis,
            egress_basis=args.egress_basis,
            allow_removals=args.allow_removals,
            batch_size=settings.embedding_batch_size,
            emit_texts=emit,
            backup=backup or None,
        )
        report = await run_import(db, export, embedder, options)
    except ImportBusy:
        print("BUSY another NHprep import or purge holds the lock")
        return 3
    except ImportRefused as refused:
        print(f"REFUSED {refused}")
        return 1
    finally:
        await db.dispose()
    print(json.dumps(report, indent=1, default=str))
    print("APPLIED" if report["applied"] else "NOT APPLIED (dry run)")
    return 0


def main() -> None:
    from app.nhprep.importer import ACCESS_BASIS
    from app.nhprep.validate import read_export
    from app.nhprep.vectors import PrecomputedEmbeddings, read_vectors

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("src", nargs="?", help="a run folder, its export.jsonl, or - (stdin)")
    parser.add_argument("--sha256", help="the expected sha256 of export.jsonl")
    parser.add_argument("--apply", action="store_true", help="write (default: a dry run)")
    parser.add_argument("--operator", help="who runs it (recorded); then --backup or --backup-waiver is required")
    parser.add_argument("--backup", help="the dump taken before this run")
    parser.add_argument("--backup-waiver", help="why there is no dump")
    parser.add_argument("--access-basis", default=ACCESS_BASIS, help="recorded with the run")
    parser.add_argument("--egress-basis", help="why the texts may go to the embedding provider (recorded)")
    parser.add_argument("--vectors", help="an nhprep-vectors/1 file: the run's only embedder")
    parser.add_argument("--emit-texts", help="dry run: write every distinct search text as {k, t} lines")
    parser.add_argument("--allow-removals", type=int, default=0, help="removals a complete export may make")
    parser.add_argument("--purge", action="store_true", help="delete the whole NHprep catalogue")
    parser.add_argument("--reason", help="why the purge (recorded)")
    args = parser.parse_args()
    problem = _check_args(args)
    if problem:
        parser.error(problem)
    export = vectors = None
    if not args.purge:
        export = read_export(*_source(args.src), args.sha256)
    if args.vectors:
        try:
            with open(args.vectors, encoding="utf-8") as stream:
                vectors = PrecomputedEmbeddings(*read_vectors(stream))
        except ValueError as error:
            print(f"REFUSED {error}")
            sys.exit(1)
    if args.emit_texts:
        with open(args.emit_texts, "w", encoding="utf-8", newline="\n") as emit:
            sys.exit(asyncio.run(run(args, export, vectors, emit)))
    sys.exit(asyncio.run(run(args, export, vectors, None)))


if __name__ == "__main__":
    main()
