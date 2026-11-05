"""Question received -> first model request: p50, p95 and p99 of real turns (read-only).

    python scripts/pre_llm_report.py [--hours 24] [--budget-ms 1000]

Each reply records `timing.preLlmMs` (internal metadata, app/chat/service.py): from when the web app's BFF had the
whole request (`X-NetSec-Received-At`; the API's own clock when absent) to when the innermost provider sent its
first request. That covers authentication, the turn's transaction, history, memory, retrieval (embedding, search),
prompt assembly and masking; the provider's own connect and TLS time comes after. Reads messages' metadata only,
never their text. Exit 0 when p99 is within the budget, 1 when not, 2 when there is nothing to measure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Integer, select  # noqa: E402

from app.db.models import Message  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.settings import Settings  # noqa: E402


def percentile(values: list[int], share: float) -> int:
    """Nearest-rank percentile of a non-empty list (share in 0..1)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summary(values: list[int], budget_ms: int) -> dict:
    if not values:
        return {"turns": 0}
    p99 = percentile(values, 0.99)
    return {
        "turns": len(values),
        "p50Ms": percentile(values, 0.50),
        "p95Ms": percentile(values, 0.95),
        "p99Ms": p99,
        "maxMs": max(values),
        "overBudget": sum(1 for value in values if value > budget_ms),
        "budgetMs": budget_ms,
        "pass": p99 <= budget_ms,
    }


async def collect(db, since: datetime) -> list[int]:
    statement = select(Message.metadata_["timing"]["preLlmMs"].astext.cast(Integer)).where(
        Message.role == "assistant",
        Message.created_at >= since,
        Message.metadata_["timing"]["preLlmMs"].isnot(None),
    )
    return [int(value) for value in await db.scalars(statement) if value is not None]


async def _run(hours: float, budget_ms: int) -> dict:
    database = Database(Settings())
    try:
        async with database.sessionmaker() as db:
            values = await collect(db, datetime.now(UTC) - timedelta(hours=hours))
    finally:
        await database.dispose()
    return {"windowHours": hours, **summary(values, budget_ms)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--budget-ms", type=int, default=1000)
    args = parser.parse_args(argv)
    report = asyncio.run(_run(args.hours, args.budget_ms))
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["turns"]:
        return 2
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
