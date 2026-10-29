"""Who voice mode is for, besides the rollout (VOICE_MODE): users whose reply language is English or unset.
OllaLink's speech output has no Hindi (provider.py), so a Hindi or Hinglish user is not offered it."""

from __future__ import annotations

from app.learning.context import StudentContext


def speaks_their_language(student: StudentContext | None) -> bool:
    """`auto` is left out of a user's preferences (app/learning/context.py), so unset reads as None."""
    language = dict(student.preferences).get("language") if student else None
    return language in (None, "english")
