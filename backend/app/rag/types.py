"""Plain data shared across the RAG layer (no I/O, no framework imports)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccessScope:
    """Whose knowledge may be searched: global sources plus this user's private ones.

    user_id None = global sources only. Always derived from the verified session,
    never from a request body. `documents`: a program chat's month (app/programs): only these
    Knowledge Library documents of approved global sources, nothing else (empty: nothing at all);
    None: no such restriction.
    """

    user_id: uuid.UUID | None
    documents: tuple[uuid.UUID, ...] | None = None


@dataclass(frozen=True)
class Curriculum:
    """What a program chat may use (app/programs), resolved by the chat service from the stored
    conversation and the owner's mapping, never from a request: the month's Knowledge Library
    documents and NHprep courses (the grounding leg, PROGRAMS_NHPREP_GROUNDING)."""

    documents: tuple[uuid.UUID, ...] = ()
    courses: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class RetrievalOptions:
    """Per-turn retrieval settings, chosen server-side by resolve_assistant_config.

    None values fall back to settings (RAG_TOP_K, RAG_MIN_SCORE). source_names and
    metadata_filter can only narrow what the AccessScope allows. classes and subjects are the
    student's curriculum (from their settings): the NCERT source ranks those books first and
    never excludes anything for them; the knowledge base ignores them.
    """

    k: int | None = None
    min_score: float | None = None
    source_names: tuple[str, ...] | None = None
    metadata_filter: dict[str, Any] | None = None
    classes: tuple[int, ...] = ()
    subjects: tuple[str, ...] = ()
    # The learner's course boundary (knowledge_courses slugs, from the Engineering Profile goal): () = every course.
    courses: tuple[str, ...] = ()
    # A program chat (app/programs): only its month's material; the course boundary and every fallback are off.
    curriculum: Curriculum | None = None
