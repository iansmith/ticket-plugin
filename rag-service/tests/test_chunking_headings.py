"""Phase-0 red tests for BILL-43 — heading-aware, 512-token chunking.

These describe the EXPECTED post-change behavior of the chunker and fail on the
current code (which has no `chunk_text`, no injected token counter, and never
splits comments). Per `design/rag-service-testing.md` these stay Layer-1: the
token counter is INJECTED as a fake (whitespace-word count) so no real tokenizer
/ model weights load in pytest.

Symbols are referenced through the module object (`_common.chunk_text`, etc.)
rather than `from ... import`, so collection succeeds and each test fails at
runtime (clean RED) instead of erroring at import while the symbols don't exist.
"""

from __future__ import annotations

from rag_service.harvesters import _common
from rag_service.harvesters._common import HarvestedComment, HarvestedTicket


# A deterministic, weightless stand-in for the real reranker tokenizer: count
# whitespace-separated words. Monotonic with length, so "no chunk over budget"
# is a meaningful assertion. Tests pick small budgets sized to this unit.
def _word_counter(text: str) -> int:
    return len(text.split())


def _ticket(**overrides):
    base = dict(
        source="linear",
        ticket_id="LOU-1",
        title="Title",
        description="A short description.",
        comments=[],
    )
    base.update(overrides)
    return HarvestedTicket(**base)


# ---------------------------------------------------------------------------
# chunk_text — the new shared, heading-aware splitter
# ---------------------------------------------------------------------------


def test_chunk_text_splits_on_headings():
    text = (
        "## Alpha\n\nalpha body one.\n\n"
        "## Beta\n\nbeta body two.\n"
    )
    chunks = _common.chunk_text(text, token_counter=_word_counter, max_tokens=1000)
    # Each heading becomes its own base section → at least two chunks.
    assert len(chunks) >= 2
    joined = "\n".join(chunks)
    assert "Alpha" in joined and "Beta" in joined
    # The alpha body and beta body land in different chunks.
    alpha_chunk = [c for c in chunks if "alpha body" in c][0]
    assert "beta body" not in alpha_chunk


def test_chunk_text_prefixes_heading_on_every_subpiece():
    # One heading, a body big enough to split into several pieces under a small
    # word budget. Every resulting piece must carry the heading text.
    body = " ".join(f"word{i}" for i in range(60))
    text = f"## Context\n\n{body}"
    chunks = _common.chunk_text(text, token_counter=_word_counter, max_tokens=15)
    assert len(chunks) >= 2
    assert all("Context" in c for c in chunks)


def test_chunk_text_respects_token_budget():
    body = " ".join(f"w{i}" for i in range(120))
    text = f"## H\n\n{body}"
    chunks = _common.chunk_text(text, token_counter=_word_counter, max_tokens=20)
    # No chunk exceeds the budget as measured by the injected counter.
    assert all(_word_counter(c) <= 20 for c in chunks)


def test_chunk_text_headingless_fallback():
    # Most ticket descriptions/comments have no markdown headings — must still
    # chunk (and size-split when large).
    body = " ".join(f"t{i}" for i in range(50))
    chunks = _common.chunk_text(body, token_counter=_word_counter, max_tokens=15)
    assert len(chunks) >= 2
    assert all(_word_counter(c) <= 15 for c in chunks)


def test_chunk_text_never_splits_a_code_fence():
    fence = "```\n" + "\n".join(f"line{i}" for i in range(30)) + "\n```"
    text = f"## Code\n\nintro\n\n{fence}\n\noutro"
    chunks = _common.chunk_text(text, token_counter=_word_counter, max_tokens=10)
    # The fence body must live intact inside exactly one chunk.
    holders = [c for c in chunks if "line0" in c]
    assert len(holders) == 1
    assert "line29" in holders[0]


def test_chunk_text_pieces_are_disjoint_no_overlap():
    body = " ".join(f"u{i}" for i in range(40))
    text = f"## S\n\n{body}"
    chunks = _common.chunk_text(text, token_counter=_word_counter, max_tokens=12)
    # Each unique body token appears in exactly one chunk (no overlap).
    seen: list[str] = []
    for c in chunks:
        seen.extend(tok for tok in c.split() if tok.startswith("u"))
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# chunk_ticket — wired onto chunk_text, comments now split, contiguous seq
# ---------------------------------------------------------------------------


def test_chunk_ticket_accepts_injected_token_counter():
    rows = _common.chunk_ticket(
        _ticket(comments=[HarvestedComment(body="hello world")]),
        token_counter=_word_counter,
    )
    assert rows  # does not raise on the new kwarg


def test_chunk_ticket_splits_a_long_comment():
    # The comment must genuinely exceed the cap (counted by _word_counter) for
    # the split to fire — size it off MAX_CHUNK_TOKENS so it can't silently fall
    # under the cap if that constant changes.
    long_body = " ".join(f"c{i}" for i in range(_common.MAX_CHUNK_TOKENS + 50))
    rows = _common.chunk_ticket(
        _ticket(comments=[HarvestedComment(body=long_body)]),
        token_counter=_word_counter,
    )
    comment_rows = [r for r in rows if r.kind == "comment"]
    # A long comment now fans out into multiple comment chunks (the reversal).
    assert len(comment_rows) >= 2
    assert all(_word_counter(r.text) <= _common.MAX_CHUNK_TOKENS for r in comment_rows)


def test_chunk_ticket_comment_subchunks_use_contiguous_seq_band():
    # First comment exceeds the cap (so it fans into >1 sub-chunk), proving the
    # running seq counter stays contiguous ACROSS a split comment's pieces — not
    # just across whole comments.
    long_body = " ".join(f"c{i}" for i in range(_common.MAX_CHUNK_TOKENS + 50))
    rows = _common.chunk_ticket(
        _ticket(comments=[HarvestedComment(body=long_body), HarvestedComment(body="tail")]),
        token_counter=_word_counter,
    )
    comment_seqs = [r.seq for r in rows if r.kind == "comment"]
    # Contiguous, starting at the comment band base, one running counter across
    # all comments' sub-chunks.
    assert comment_seqs == list(
        range(_common.COMMENT_SEQ_BASE, _common.COMMENT_SEQ_BASE + len(comment_seqs))
    )
    # All comment seqs stay in the comment band; descriptions stay below it.
    assert all(s >= _common.COMMENT_SEQ_BASE for s in comment_seqs)
    desc_seqs = [r.seq for r in rows if r.kind == "description"]
    assert all(s < _common.COMMENT_SEQ_BASE for s in desc_seqs)


def test_chunk_ticket_no_chunk_exceeds_token_budget():
    long_desc = " ".join(f"d{i}" for i in range(200))
    rows = _common.chunk_ticket(
        _ticket(description=long_desc, comments=[HarvestedComment(body="short")]),
        token_counter=_word_counter,
    )
    assert all(_word_counter(r.text) <= _common.MAX_CHUNK_TOKENS for r in rows)
