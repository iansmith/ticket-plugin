"""Query pre-processor: expand domain-specific terms before embedding/reranking.

Applied once per /search request, before both Stage-1 (dense embedding) and
Stage-2 (cross-encoder rerank).  Both stages receive the same expanded string,
so the expansion influences cosine-neighbour selection AND the cross-encoder's
joint (query, passage) scoring.

# Why expansion, not pure substitution

We append the domain meaning alongside the original term rather than replacing
it.  This preserves exact-token overlap with the corpus (a chunk that literally
contains "residual" still matches the query token "residual") while adding
semantic context that steers the embedding away from the general-language sense
of the word.

# Glossary curation

Add an entry whenever a search term has a common general-language sense that
diverges from its meaning in this bug-tracker corpus, AND that divergence
causes a measurable retrieval miss (confirmed by checking ticket_chunks with a
direct SQL ILIKE query before asserting absence).

Current entries and the specific mismatches they fix:
  "residual"   — general sense: "leftover amount / remainder after subtraction"
                 corpus sense:  "a regression that appeared as a side-effect of
                                 an earlier fix (a residual regression)"
                 Reranker was matching it to "resolved / fixed" narrative text
                 (LOU-66) instead of the fix-commit chunks that contain the term.

# Testing

`preprocess_query` is a pure function with no side-effects — test it directly
at Layer 1 (see design/rag-service-testing.md) without TestClient or fakes.
"""

from __future__ import annotations

import re

# Term → expanded form.  The key is matched case-insensitively as a whole word
# (\b boundaries) to avoid spurious substring hits (e.g. "residuals").
#
# Expansion format: "<original term> (<domain gloss>)".  The parenthetical keeps
# the original token present so exact-overlap scoring still fires.
GLOSSARY: dict[str, str] = {
    "residual": "residual (regression side-effect from a prior fix)",
}

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE), expansion)
    for term, expansion in GLOSSARY.items()
]


def preprocess_query(query: str) -> str:
    """Expand domain-specific terms in `query` before embedding / reranking.

    Returns the (possibly expanded) query string.  If no glossary term appears
    in the query the original string is returned unchanged, so there is zero
    overhead for queries that don't need expansion.

    Idempotent: an already-expanded query is returned unchanged, since the
    expansion text is detected and skipped on a second pass.

    Pure function — no I/O, no side-effects.  Safe to call from the FastAPI
    request handler on every /search call.
    """
    for pattern, expansion in _COMPILED:
        if expansion in query:
            continue
        query = pattern.sub(expansion, query)
    return query
