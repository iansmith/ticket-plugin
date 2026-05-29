"""Layer-1 unit tests for the shared harvester ingestion spine.

No FastAPI, no postgres, no model weights, no network — direct calls on the
pure helpers in `rag_service.harvesters._common`. Per
`design/rag-service-testing.md` this is the cheapest layer and holds the bulk
of the logic assertions. The one I/O function in that module (`write_ticket`,
which needs pgvector) is intentionally NOT tested here — it's covered by the
Docker integration gate.
"""

from __future__ import annotations

import numpy as np

from rag_service.harvesters._common import (
    COMMENT_SEQ_BASE,
    MAX_DESCRIPTION_TOKENS,
    HarvestedComment,
    HarvestedTicket,
    RateLimiter,
    chunk_ticket,
    embed_rows,
    extract_code_refs,
    extract_ticket_refs,
    strip_code_blocks,
    synthesize_code_sentence,
)


# ---------------------------------------------------------------------------
# strip_code_blocks
# ---------------------------------------------------------------------------


def test_strip_code_blocks_removes_fenced_block_and_returns_body():
    text = "before\n```go\nfunc runqGet() {}\n```\nafter"
    prose, blocks = strip_code_blocks(text)
    assert "func runqGet" not in prose
    assert prose == "before\n\nafter"
    assert blocks == ["func runqGet() {}"]


def test_strip_code_blocks_handles_tilde_fences():
    text = "x\n~~~\nraw ~~~ inside is fine\n~~~\ny"
    prose, blocks = strip_code_blocks(text)
    assert blocks == ["raw ~~~ inside is fine"]
    assert "raw" not in prose


def test_strip_code_blocks_leaves_inline_code():
    text = "use the `runqGet` helper here"
    prose, blocks = strip_code_blocks(text)
    assert prose == "use the `runqGet` helper here"
    assert blocks == []


def test_strip_code_blocks_multiple_blocks():
    text = "a\n```\none\n```\nb\n```\ntwo\n```\nc"
    prose, blocks = strip_code_blocks(text)
    assert blocks == ["one", "two"]
    assert prose == "a\n\nb\n\nc"


# ---------------------------------------------------------------------------
# extract_code_refs
# ---------------------------------------------------------------------------


def test_extract_code_refs_file_and_module():
    refs = extract_code_refs(["edit kmazarin/sched.go to fix it"])
    assert {"file": "kmazarin/sched.go", "module": "kmazarin"} in refs


def test_extract_code_refs_function_declaration():
    refs = extract_code_refs(["func runqGet() int { return 0 }"])
    assert {"func": "runqGet"} in refs


def test_extract_code_refs_filters_control_flow_keywords():
    refs = extract_code_refs(["if (x) { for (y) { return f(z) } }"])
    funcs = {r.get("func") for r in refs}
    assert "if" not in funcs and "for" not in funcs and "return" not in funcs
    assert "f" in funcs  # a genuine call survives


def test_extract_code_refs_rejects_version_numbers_as_files():
    # "2.8.0" must NOT be read as a file with extension "0".
    refs = extract_code_refs(["torch==2.8.0 is pinned"])
    assert all("file" not in r for r in refs)


def test_extract_code_refs_deduplicates_and_is_deterministic():
    block = "foo.py foo.py bar() bar()"
    assert extract_code_refs([block]) == extract_code_refs([block])
    refs = extract_code_refs([block])
    assert sum(1 for r in refs if r.get("file") == "foo.py") == 1


# ---------------------------------------------------------------------------
# synthesize_code_sentence
# ---------------------------------------------------------------------------


def test_synthesize_code_sentence_function_and_file():
    refs = [{"file": "kmazarin/sched.go", "module": "kmazarin"}, {"func": "runqGet"}]
    sentence = synthesize_code_sentence(refs)
    assert sentence == (
        "This text references function `runqGet` in file `kmazarin/sched.go`."
    )


def test_synthesize_code_sentence_empty_is_empty_string():
    assert synthesize_code_sentence([]) == ""


def test_synthesize_code_sentence_pluralizes():
    refs = [{"func": "a"}, {"func": "b"}]
    assert synthesize_code_sentence(refs) == "This text references functions `a` and `b`."


# ---------------------------------------------------------------------------
# extract_ticket_refs
# ---------------------------------------------------------------------------


def test_extract_ticket_refs_prefixed():
    assert extract_ticket_refs("see MAZ-15 and LOU-94") == ["LOU-94", "MAZ-15"]


def test_extract_ticket_refs_github_full_and_bare():
    refs = extract_ticket_refs("fixes iansmith/mazzy#42 and also #7")
    assert refs == ["#7", "iansmith/mazzy#42"]


def test_extract_ticket_refs_bare_not_double_counted():
    # The '#42' tail of owner/repo#42 must not also produce a bare '#42'.
    refs = extract_ticket_refs("iansmith/mazzy#42")
    assert refs == ["iansmith/mazzy#42"]


def test_extract_ticket_refs_dedup_and_sorted():
    assert extract_ticket_refs("MAZ-1 MAZ-1 MAZ-1") == ["MAZ-1"]


def test_extract_ticket_refs_none_found():
    assert extract_ticket_refs("plain prose, no refs") == []


# ---------------------------------------------------------------------------
# chunk_ticket
# ---------------------------------------------------------------------------


def _ticket(**kw) -> HarvestedTicket:
    base = dict(
        source="linear",
        ticket_id="LOU-102",
        title="Residual pixel shift",
        description="The multicol-breaking-001 case shifts by one pixel.",
    )
    base.update(kw)
    return HarvestedTicket(**base)


def test_chunk_ticket_description_is_seq0_and_includes_title():
    rows = chunk_ticket(_ticket(comments=[]))
    assert len(rows) == 1
    desc = rows[0]
    assert desc.kind == "description"
    assert desc.seq == 0
    assert "Residual pixel shift" in desc.text  # title folded in
    assert "multicol-breaking-001" in desc.text


def test_chunk_ticket_comments_use_seq_band():
    rows = chunk_ticket(
        _ticket(
            comments=[
                HarvestedComment(body="first", author="ian", upstream_id="c1"),
                HarvestedComment(body="second", author="lou", upstream_id="c2"),
            ]
        )
    )
    assert [r.seq for r in rows] == [0, COMMENT_SEQ_BASE, COMMENT_SEQ_BASE + 1]
    assert [r.kind for r in rows] == ["description", "comment", "comment"]
    assert rows[1].author == "ian" and rows[1].upstream_id == "c1"


def test_chunk_ticket_skips_empty_comments_without_seq_gap():
    rows = chunk_ticket(
        _ticket(
            comments=[
                HarvestedComment(body="kept-1"),
                HarvestedComment(body="   "),  # skipped
                HarvestedComment(body="kept-2"),
            ]
        )
    )
    # The skipped comment must not leave a seq hole — surviving comments are
    # contiguous from the band base.
    comment_seqs = [r.seq for r in rows if r.kind == "comment"]
    assert comment_seqs == [COMMENT_SEQ_BASE, COMMENT_SEQ_BASE + 1]


def test_chunk_ticket_skips_empty_comments():
    rows = chunk_ticket(_ticket(comments=[HarvestedComment(body="   ")]))
    assert len(rows) == 1  # only the description


# ---------------------------------------------------------------------------
# chunk_ticket — oversized-description splitting
# ---------------------------------------------------------------------------


def _huge_description() -> str:
    # Each paragraph ~ a few hundred chars; enough of them to blow past the
    # MAX_DESCRIPTION_TOKENS budget (chars/4 estimate).
    target_chars = (MAX_DESCRIPTION_TOKENS + 2000) * 4
    para = (
        "This is paragraph number {n}. " + ("lorem ipsum dolor sit amet " * 12)
    )
    paras, total, n = [], 0, 0
    while total < target_chars:
        p = para.format(n=n)
        paras.append(p)
        total += len(p) + 2
        n += 1
    return "\n\n".join(paras)


def test_chunk_ticket_splits_oversized_description():
    rows = chunk_ticket(_ticket(description=_huge_description(), comments=[]))
    desc_rows = [r for r in rows if r.kind == "description"]
    assert len(desc_rows) > 1  # actually split
    # Contiguous seqs starting at 0, all inside the description band.
    assert [r.seq for r in desc_rows] == list(range(len(desc_rows)))
    assert all(r.seq < COMMENT_SEQ_BASE for r in desc_rows)


def test_oversized_description_keeps_comments_in_band():
    rows = chunk_ticket(
        _ticket(
            description=_huge_description(),
            comments=[HarvestedComment(body="a real comment")],
        )
    )
    n_desc = sum(1 for r in rows if r.kind == "description")
    comment = next(r for r in rows if r.kind == "comment")
    # The comment seq is unaffected by how many pieces the description made.
    assert comment.seq == COMMENT_SEQ_BASE
    assert n_desc > 1


def test_oversized_description_has_overlap_between_chunks():
    rows = [r for r in chunk_ticket(_ticket(description=_huge_description(), comments=[]))
            if r.kind == "description"]
    # Single-unit overlap: the start of chunk i+1 should re-include the last
    # paragraph of chunk i, so consecutive chunks share some text.
    shared = [
        bool(set(rows[i].text.split("\n\n")) & set(rows[i + 1].text.split("\n\n")))
        for i in range(len(rows) - 1)
    ]
    assert all(shared)


# ---------------------------------------------------------------------------
# write_ticket — resync-scope identity guard
#
# write_ticket's DB body needs pgvector (covered by the Docker gate), but the
# pre-INSERT validation loop is pure Python and worth a Layer-1 test. We only
# need a connection whose .transaction()/.cursor() never get reached — the
# guard raises before the transaction opens — so a bare object suffices.
# ---------------------------------------------------------------------------


def _embedded_row(**overrides) -> ChunkRow:
    row = ChunkRow(
        source="linear",
        ticket_id="LOU-102",
        provenance="upstream",
        kind="description",
        seq=0,
        text="x",
        code_refs=[],
        ticket_refs=[],
    )
    row.embedding = [0.0] * 1024
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


class _ExplodingConn:
    """A connection that fails loudly if write_ticket ever touches the DB —
    proving the guard rejects a bad batch *before* opening a transaction."""

    def transaction(self):
        raise AssertionError("write_ticket opened a transaction despite a bad row")

    def cursor(self):
        raise AssertionError("write_ticket used a cursor despite a bad row")


def test_write_ticket_rejects_row_outside_resync_scope():
    # A row whose ticket_id differs from the function's resync scope must be
    # rejected before any DB work — else the DELETE (scoped by the args) would
    # clear one ticket while the INSERT writes another, leaving stale rows.
    rows = [_embedded_row(ticket_id="LOU-999")]
    with pytest.raises(ValueError, match="outside the resync"):
        write_ticket(
            _ExplodingConn(), rows, source="linear", ticket_id="LOU-102"
        )


def test_write_ticket_rejects_unembedded_row_first():
    row = _embedded_row()
    row.embedding = None
    with pytest.raises(ValueError, match="un-embedded"):
        write_ticket(
            _ExplodingConn(), [row], source="linear", ticket_id="LOU-102"
        )


def test_oversized_description_never_splits_a_code_fence():
    # A fenced block embedded in a huge description must survive intact in
    # exactly one chunk — extracted as a code_ref, never cut in half.
    body = _huge_description() + "\n\n```go\nfunc criticalFn() {}\n```"
    rows = chunk_ticket(_ticket(description=body, comments=[]))
    fn_rows = [r for r in rows if any(c.get("func") == "criticalFn" for c in r.code_refs)]
    assert len(fn_rows) >= 1  # the fence landed in (at least) one chunk's refs


def test_chunk_ticket_strips_code_and_records_refs():
    rows = chunk_ticket(
        _ticket(
            description="root cause below\n```go\nfunc runqGet() {}\n```\nsee MAZ-9",
            comments=[],
        )
    )
    desc = rows[0]
    assert "func runqGet" not in desc.text  # code fence stripped
    assert "references function `runqGet`" in desc.text  # synthesized sentence
    assert {"func": "runqGet"} in desc.code_refs
    assert "MAZ-9" in desc.ticket_refs  # ref extracted from raw text


def test_chunk_ticket_propagates_source_and_ticket_id():
    rows = chunk_ticket(_ticket(source="linear", ticket_id="LOU-102", comments=[]))
    assert all(r.source == "linear" and r.ticket_id == "LOU-102" for r in rows)
    assert all(r.provenance == "upstream" for r in rows)


# ---------------------------------------------------------------------------
# embed_rows
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Deterministic 1024-dim encoder; vector seeded by text length so distinct
    texts get distinct vectors. Mirrors rag_service.embed.Embedder."""

    def encode_passage(self, text: str) -> np.ndarray:
        return np.full(1024, float(len(text) % 7), dtype=np.float32)


def test_embed_rows_fills_embedding_as_plain_list():
    rows = chunk_ticket(_ticket(comments=[HarvestedComment(body="hello")]))
    out = embed_rows(rows, _FakeEmbedder())
    assert out is rows  # mutates in place
    for r in rows:
        assert isinstance(r.embedding, list)
        assert len(r.embedding) == 1024
        assert all(isinstance(x, float) for x in r.embedding[:3])


# ---------------------------------------------------------------------------
# RateLimiter (virtual clock — no real waiting)
# ---------------------------------------------------------------------------


class _VirtualClock:
    """Drives RateLimiter without wall-clock time: sleep() just advances now()."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _limiter(clock: _VirtualClock, **kw) -> RateLimiter:
    return RateLimiter(clock=clock.now, sleep=clock.sleep, **kw)


def test_rate_limiter_blocks_when_window_full():
    clock = _VirtualClock()
    rl = _limiter(clock, max_calls=2, period_s=10)
    rl.acquire()  # t=0
    rl.acquire()  # t=0, window now full
    rl.acquire()  # must wait out the period
    assert clock.t == 10  # slept until the oldest call aged out


def test_rate_limiter_allows_within_budget_without_sleeping():
    clock = _VirtualClock()
    rl = _limiter(clock, max_calls=30, period_s=3600)
    for _ in range(30):
        rl.acquire()
    assert clock.t == 0  # 30 calls in a 30/hr budget: no throttling


def test_rate_limiter_enforces_minimum_interval():
    clock = _VirtualClock()
    rl = _limiter(clock, max_calls=1000, period_s=3600, min_interval_s=1.0)
    rl.acquire()  # t=0
    rl.acquire()  # spaced 1s
    rl.acquire()  # spaced another 1s
    assert clock.t == 2.0
