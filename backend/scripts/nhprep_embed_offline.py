"""Embed NHprep search texts where the embedding key lives (the host), for an import elsewhere (spec r2-data-search §4).

    python scripts/nhprep_import.py <bundle> --emit-texts texts.jsonl          # PC or WSL: {"k", "t"} lines
    cat texts.jsonl | systemd-run --pipe --wait --collect -p EnvironmentFile=/etc/netsecai/api.env \\
        -p WorkingDirectory=/opt/netsecai/app/backend \\
        /opt/netsecai/app/backend/.venv/bin/python scripts/nhprep_embed_offline.py > vectors.jsonl
    python scripts/nhprep_import.py <bundle> --apply --vectors vectors.jsonl ...   # WSL: no provider call

Each line's key must be the sha256 of its text; duplicates are embedded once. Texts go through build_embeddings
(secrets masked), sequentially in batches of EMBEDDING_BATCH_SIZE with the importer's retries. Writes the
nhprep-vectors/1 file to stdout and only counts to stderr, never a text.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _nothing() -> None:
    return None


async def run() -> int:
    from app.nhprep.embedding import embed_texts
    from app.nhprep.vectors import text_key, write_vectors
    from app.rag.embeddings import build_embeddings, embedding_model_id
    from app.settings import get_settings

    texts: dict[str, str] = {}
    for number, line in enumerate(sys.stdin, 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item.get("t"), str) or text_key(item["t"]) != item.get("k"):
            print(f"REFUSED line {number}: the key is not the sha256 of the text", file=sys.stderr)
            return 1
        texts.setdefault(item["k"], item["t"])
    settings = get_settings()
    embeddings = build_embeddings(settings)  # masked: secrets never reach the provider
    keys = list(texts)
    vectors: list[list[float]] = []
    size = settings.embedding_batch_size
    for start in range(0, len(keys), size):
        vectors += await embed_texts(embeddings, [texts[key] for key in keys[start : start + size]], _nothing)
    write_vectors(sys.stdout, embedding_model_id(embeddings), list(zip(keys, vectors, strict=True)))
    print(f"embedded {len(keys)} texts with {embedding_model_id(embeddings)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
