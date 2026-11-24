"""Which questions may decide a competition (plan section 5, invariant I18; owner: CD3, R-E1, CD18).

One predicate, used when a battle is offered (the pool), when it starts (the pick) and when each round closes (the
question still eligible, with the content it was served with). A question is competitive only when all hold:

1. active;
2. single answer: `mcq` with exactly one key (either shape);
3. verified for its exact current content: human approval, which the database binds (`reviewed_hash =
   content_hash`; an edit removes it); or, under FRIEND_ZONE_KEY_POLICY=human_or_ai (owner U1, option ii), an AI
   blind check in qb_key_checks for this exact content_hash, on an authored question no person has rejected;
4. its key is not NHprep's (U2 undecided: excluded even when approved);
5. short enough to read in a round: at most MAX_WORDS words of stem and options.

Never decided by a client."""

from __future__ import annotations

from sqlalchemy import and_, exists, func, literal_column, or_

from app.db.models import QbKeyCheck, QbQuestion

MAX_WORDS = 105  # R-T1: longer questions are not battle-eligible

# The words a student reads: the stem and every option, in either stored shape.
WORDS = literal_column(
    "cardinality(regexp_split_to_array(btrim(concat_ws(' ', qb_questions.stem, qb_questions.option_a, "
    "qb_questions.option_b, qb_questions.option_c, qb_questions.option_d, "
    "(SELECT string_agg(o, ' ') FROM jsonb_array_elements_text(COALESCE(qb_questions.options, '[]'::jsonb)) o))), "
    "'\\s+'))"
)


def verified(policy: str):
    """SQL: the question's key is verified for its current content, as the policy allows."""
    human = and_(QbQuestion.review_status == "approved", QbQuestion.reviewed_hash == QbQuestion.content_hash)
    if policy != "human_or_ai":
        return human
    ai = and_(
        QbQuestion.key_origin == "authored",
        QbQuestion.review_status != "rejected",
        exists().where(
            QbKeyCheck.question_id == QbQuestion.question_id,
            QbKeyCheck.content_hash == QbQuestion.content_hash,
            QbKeyCheck.single_defensible,
        ),
    )
    return or_(human, ai)


def competitive(policy: str = "human_only"):
    """SQL: this question may be used in a battle or a daily challenge."""
    return and_(
        QbQuestion.is_active,
        QbQuestion.question_format == "mcq",
        or_(QbQuestion.correct_option.is_not(None), func.cardinality(QbQuestion.correct_keys) == 1),
        verified(policy),
        or_(QbQuestion.key_origin.is_(None), QbQuestion.key_origin != "nhprep"),
        WORDS <= MAX_WORDS,
    )
