"""Unit tests for the Linear harvester (BILL-37).

No live Linear API, no postgres, no model weights — per
`design/rag-service-testing.md`. Collaborators are injected:

  - `FakeLinearClient`: canned `HarvestedTicket`s for exercising the
    sync_ticket / sync_recent ingestion path without network.
  - `_FakeEmbedder`: deterministic 1024-dim vectors.
  - `_RecordingConn`: a stand-in for a psycopg connection that records the
    rows `write_ticket` would persist, so we can assert on assembled rows
    without pgvector.
  - The real `LinearGraphQLClient` is tested directly over
    `httpx.MockTransport` (no live API): response parsing, the dual
    request/complexity budgets, header reconciliation, and Linear's
    HTTP-400 + `RATELIMITED` throttle signal with exponential backoff.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

import httpx

from rag_service.harvesters._common import (
    COMMENT_SEQ_BASE,
    ComplexityBudget,
    HarvestedComment,
    HarvestedTicket,
    RateLimiter,
)
from rag_service.harvesters.linear import (
    LINEAR_BATCH_SIZE,
    LINEAR_GRAPHQL_ENDPOINT,
    LINEAR_MAX_QUERY_COMPLEXITY,
    LinearGraphQLClient,
    LinearRateLimitError,
    issue_complexity,
    page_complexity,
    parse_identifier,
    resolve_linear_api_key,
    sync_recent,
    sync_ticket,
    team_key_for,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


# Weightless stand-in for the reranker tokenizer (whitespace-word count), injected
# into the sync path so chunk_ticket never loads real model weights in pytest
# (`design/rag-service-testing.md`). The test tickets are tiny, so every chunk
# stays well under the cap — desc and comment each produce a single chunk.
def _word_counter(text: str) -> int:
    return len(text.split())


class _FakeEmbedder:
    def encode_passage(self, text: str) -> np.ndarray:
        return np.full(1024, float(len(text) % 7), dtype=np.float32)


class _RecordingCursor:
    def __init__(self, conn: _RecordingConn) -> None:
        self._conn = conn

    def execute(self, sql: str, params=None) -> None:
        if sql.strip().upper().startswith("DELETE"):
            self._conn.deletes.append(params)
        else:
            self._conn.inserts.append(params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingConn:
    """Minimal psycopg-Connection stand-in for write_ticket.

    Records DELETE/INSERT param tuples and supports the `with conn.transaction()`
    + `with conn.cursor()` context-manager shape write_ticket uses. No real SQL
    runs; pgvector behavior is covered by the Docker gate, not here.
    """

    def __init__(self) -> None:
        self.deletes: list = []
        self.inserts: list = []

    def transaction(self):
        conn = self

        class _Txn:
            def __enter__(self):
                return conn

            def __exit__(self, *exc):
                return False

        return _Txn()

    def cursor(self):
        return _RecordingCursor(self)


class _VirtualClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeLinearClient:
    """Canned-response LinearClient for the sync orchestration tests.

    `tickets` maps identifier -> HarvestedTicket for fetch_ticket. `recent` is
    the full list fetch_recent yields. Rate limiting / pagination / budgeting
    are NOT modelled here — those live in the real `LinearGraphQLClient` and are
    tested directly against it (with httpx.MockTransport) further down. This
    fake exists to exercise the `sync_ticket` / `sync_recent` ingestion path in
    isolation, with no network.
    """

    def __init__(
        self,
        *,
        tickets: dict[str, HarvestedTicket] | None = None,
        recent: list[HarvestedTicket] | None = None,
    ) -> None:
        self._tickets = tickets or {}
        self._recent = recent or []
        self.fetch_ticket_calls: list[str] = []
        self.fetch_recent_calls: list[datetime] = []

    def fetch_ticket(self, identifier: str) -> HarvestedTicket | None:
        self.fetch_ticket_calls.append(identifier)
        return self._tickets.get(identifier)

    def fetch_recent(self, since: datetime) -> list[HarvestedTicket]:
        self.fetch_recent_calls.append(since)
        return list(self._recent)


# ---------------------------------------------------------------------------
# parse_identifier / team_key_for
# ---------------------------------------------------------------------------


def test_parse_identifier_splits_team_and_number():
    assert parse_identifier("LOU-102") == ("LOU", 102)
    assert parse_identifier("MAZ-43") == ("MAZ", 43)


def test_parse_identifier_rejects_malformed():
    for bad in ["lou-102", "LOU", "LOU-", "-12", "LOU_12"]:
        with pytest.raises(ValueError):
            parse_identifier(bad)


def test_team_key_for_defaults_to_identifier_prefix():
    assert team_key_for("LOU-102") == "LOU"


def test_team_key_for_honors_override_map():
    assert team_key_for("LOU-102", {"LOU": "louis-team"}) == "louis-team"


# ---------------------------------------------------------------------------
# resolve_linear_api_key — env-var-first, then .harvester.toml
# ---------------------------------------------------------------------------


def test_resolve_key_env_wins(monkeypatch, tmp_path):
    # Env var must win even when the file also has a key.
    conf = tmp_path / ".harvester.toml"
    conf.write_text('[linear]\napi_key = "from_file"\n')
    monkeypatch.setenv("LINEAR_API_KEY", "from_env")
    assert resolve_linear_api_key(str(conf)) == "from_env"


def test_resolve_key_falls_back_to_toml(monkeypatch, tmp_path):
    conf = tmp_path / ".harvester.toml"
    conf.write_text('[linear]\napi_key = "from_file"\n')
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert resolve_linear_api_key(str(conf)) == "from_file"


def test_resolve_key_none_when_neither(monkeypatch, tmp_path):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    # Missing file -> None (not an error).
    assert resolve_linear_api_key(str(tmp_path / "absent.toml")) is None
    # File present but no [linear].api_key -> None.
    empty = tmp_path / ".harvester.toml"
    empty.write_text('[jira]\nemail = "x"\n')
    assert resolve_linear_api_key(str(empty)) is None


# ---------------------------------------------------------------------------
# sync_ticket
# ---------------------------------------------------------------------------


def _ticket(identifier="LOU-102", **kw) -> HarvestedTicket:
    base = dict(
        source="linear",
        ticket_id=identifier,
        title="Residual pixel shift",
        description="multicol-breaking-001 shifts by one pixel.",
        comments=[HarvestedComment(body="root cause in finalBlockSize gating")],
    )
    base.update(kw)
    return HarvestedTicket(**base)


def test_sync_ticket_writes_chunks_for_found_ticket():
    client = FakeLinearClient(tickets={"LOU-102": _ticket()})
    conn = _RecordingConn()
    n = sync_ticket(
        "LOU-102", client=client, conn=conn, embedder=_FakeEmbedder(),
        token_counter=_word_counter,
    )
    assert n == 2  # description + one comment
    assert client.fetch_ticket_calls == ["LOU-102"]
    assert len(conn.inserts) == 2
    # Full-resync: exactly one scoped DELETE before the inserts.
    assert len(conn.deletes) == 1
    assert conn.deletes[0] == ("linear", "LOU-102", "upstream")


def test_sync_ticket_missing_ticket_is_noop():
    client = FakeLinearClient(tickets={})
    conn = _RecordingConn()
    n = sync_ticket("LOU-999", client=client, conn=conn, embedder=_FakeEmbedder())
    assert n == 0
    assert conn.inserts == [] and conn.deletes == []


def test_sync_ticket_embeds_every_row():
    client = FakeLinearClient(tickets={"LOU-102": _ticket()})
    conn = _RecordingConn()
    sync_ticket(
        "LOU-102", client=client, conn=conn, embedder=_FakeEmbedder(),
        token_counter=_word_counter,
    )
    # write_ticket binds embedding as a 1024-element list; find it in each
    # INSERT param tuple.
    for params in conn.inserts:
        vec = next(p for p in params if isinstance(p, list) and len(p) == 1024)
        assert all(isinstance(x, float) for x in vec[:3])


def test_sync_ticket_assigns_seq_bands():
    # Description seq 0; the comment lands in the comment band (0x1000).
    client = FakeLinearClient(tickets={"LOU-102": _ticket()})
    conn = _RecordingConn()
    sync_ticket(
        "LOU-102", client=client, conn=conn, embedder=_FakeEmbedder(),
        token_counter=_word_counter,
    )
    # seq is the 6th INSERT column (source,ticket_id,project,provenance,kind,seq,...).
    seqs = sorted(params[5] for params in conn.inserts)
    assert seqs == [0, COMMENT_SEQ_BASE]


# ---------------------------------------------------------------------------
# sync_recent — batching + rate-limit budget
# ---------------------------------------------------------------------------


def test_sync_recent_ingests_all_tickets():
    recent = [_ticket(identifier=f"LOU-{i}") for i in range(5)]
    client = FakeLinearClient(recent=recent)
    conn = _RecordingConn()
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    n = sync_recent(
        since, client=client, conn=conn, embedder=_FakeEmbedder(),
        token_counter=_word_counter,
    )
    assert n == 10  # 5 tickets x (1 description + 1 comment)
    assert len(conn.deletes) == 5  # one full-resync DELETE per ticket


def test_sync_recent_ingests_large_corpus():
    recent = [_ticket(identifier=f"LOU-{i}") for i in range(120)]
    client = FakeLinearClient(recent=recent)
    conn = _RecordingConn()
    sync_recent(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        client=client,
        conn=conn,
        embedder=_FakeEmbedder(),
        token_counter=_word_counter,
    )
    assert len(conn.inserts) == 240  # 120 tickets x (description + comment)
    assert len(conn.deletes) == 120  # one full-resync DELETE per ticket


# ---------------------------------------------------------------------------
# Complexity estimation (matches design §Rate-limit budgets derivation)
# ---------------------------------------------------------------------------


def test_issue_complexity_matches_documented_scoring():
    # 5 scalar props (0.5) + comments(first:100) at 2.4 pts each = 240.5.
    assert issue_complexity(comments_per_page=100) == pytest.approx(240.5)


def test_page_complexity_includes_issue_object_multiplier():
    # Each issue child = 1 (object) + issue_complexity(); times num_issues.
    assert page_complexity(1, comments_per_page=100) == pytest.approx(241.5)
    assert page_complexity(40, comments_per_page=100) == pytest.approx(241.5 * 40)


def test_batch_size_stays_under_single_query_cap():
    # The chosen LINEAR_BATCH_SIZE must keep one page under Linear's 10K-pt
    # per-query ceiling — the constraint that fixed the batch size at 40.
    assert page_complexity(LINEAR_BATCH_SIZE) < LINEAR_MAX_QUERY_COMPLEXITY
    # ...and the size is near (not needlessly far below) the ceiling: 42 issues
    # would breach it (42 * 241.5 = 10143 > 10000).
    assert page_complexity(42) > LINEAR_MAX_QUERY_COMPLEXITY


# ---------------------------------------------------------------------------
# ComplexityBudget — point-based leaky bucket
# ---------------------------------------------------------------------------


def test_complexity_budget_blocks_until_points_refill():
    clock = _VirtualClock()
    # 100 points over 100s -> 1 pt/sec refill; bucket starts full at 100.
    cb = ComplexityBudget(
        max_points=100, period_s=100, clock=clock.now, sleep=clock.sleep
    )
    cb.reserve(100)  # drains the bucket at t=0
    cb.reserve(50)   # must wait 50s for 50 pts to refill
    assert clock.t == 50.0


def test_complexity_budget_within_budget_does_not_sleep():
    clock = _VirtualClock()
    cb = ComplexityBudget(
        max_points=1000, period_s=100, clock=clock.now, sleep=clock.sleep
    )
    cb.reserve(200)
    cb.reserve(300)
    assert clock.t == 0.0  # 500 of 1000 pts: no throttle


def test_complexity_budget_observe_snaps_down_to_server_header():
    clock = _VirtualClock()
    cb = ComplexityBudget(
        max_points=1000, period_s=100, clock=clock.now, sleep=clock.sleep
    )
    cb.reserve(100)  # local model: 900 remaining
    cb.observe(10)   # server says only 10 left — trust it
    # Next reserve of 50 must now wait for 40 pts to refill (10 pt/sec rate).
    cb.reserve(50)
    assert clock.t == pytest.approx(4.0)


def test_complexity_budget_rejects_impossible_single_reservation():
    cb = ComplexityBudget(max_points=100, period_s=100)
    with pytest.raises(ValueError):
        cb.reserve(101)  # bigger than the whole bucket — can never satisfy


# ---------------------------------------------------------------------------
# LinearGraphQLClient — real client over httpx.MockTransport (no network)
# ---------------------------------------------------------------------------


_ISSUE_NODE = {
    "id": "uuid-1",
    "identifier": "LOU-102",
    "title": "Residual pixel shift",
    "description": "multicol-breaking-001 shifts by one pixel.",
    "url": "https://linear.app/lou/issue/LOU-102",
    "comments": {"nodes": [
        {"id": "c1", "body": "root cause in finalBlockSize gating",
         "createdAt": "2026-01-02T00:00:00.000Z", "user": {"name": "ian"}},
    ]},
}


def _mock_client(
    handler, *, rate_limiter=None, complexity_budget=None, **kw
) -> LinearGraphQLClient:
    """Build a real LinearGraphQLClient over a MockTransport, with virtual-clock
    budgets by default so retry/backoff tests never incur real `time.sleep`."""
    clock = _VirtualClock()
    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=LINEAR_GRAPHQL_ENDPOINT
    )
    rl = rate_limiter or RateLimiter(
        max_calls=10_000, period_s=3600, clock=clock.now, sleep=clock.sleep
    )
    cb = complexity_budget or ComplexityBudget(
        max_points=3_000_000, period_s=3600, clock=clock.now, sleep=clock.sleep
    )
    return LinearGraphQLClient(
        "fake-key", http_client=http, rate_limiter=rl, complexity_budget=cb, **kw
    )


def test_client_fetch_ticket_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"issues": {"nodes": [_ISSUE_NODE]}}},
            headers={"X-RateLimit-Complexity-Remaining": "2999000"},
        )

    client = _mock_client(handler)
    ticket = client.fetch_ticket("LOU-102")
    assert ticket is not None
    assert ticket.ticket_id == "LOU-102"
    assert ticket.source == "linear"
    assert len(ticket.comments) == 1
    assert ticket.comments[0].author == "ian"


def test_client_retries_on_ratelimited_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # Linear signals throttling as HTTP 400 + RATELIMITED (NOT 429).
            return httpx.Response(
                400,
                json={"errors": [{"message": "rate limited",
                                  "extensions": {"code": "RATELIMITED"}}]},
            )
        return httpx.Response(
            200, json={"data": {"issues": {"nodes": [_ISSUE_NODE]}}}
        )

    client = _mock_client(handler, sleep=sleeps.append)
    ticket = client.fetch_ticket("LOU-102")
    assert ticket is not None
    assert calls["n"] == 2  # one throttle, one success
    assert sleeps == [1]  # one backoff of 2**0 == 1s


def test_client_raises_after_persistent_ratelimit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"errors": [{"extensions": {"code": "RATELIMITED"}}]},
        )

    client = _mock_client(handler, max_retries=2, sleep=lambda s: None)
    with pytest.raises(LinearRateLimitError):
        client.fetch_ticket("LOU-102")


def test_client_raises_on_generic_graphql_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": [{"message": "field 'bogus' doesn't exist"}]}
        )

    client = _mock_client(handler)
    with pytest.raises(RuntimeError):
        client.fetch_ticket("LOU-102")


def test_client_reconciles_complexity_from_header():
    clock = _VirtualClock()
    cb = ComplexityBudget(
        max_points=3_000_000, period_s=3600, clock=clock.now, sleep=clock.sleep
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # Server claims almost no budget left, contradicting our local estimate.
        return httpx.Response(
            200,
            json={"data": {"issues": {"nodes": [_ISSUE_NODE]}}},
            headers={"X-RateLimit-Complexity-Remaining": "100"},
        )

    client = _mock_client(handler, complexity_budget=cb)
    client.fetch_ticket("LOU-102")
    # After observe(100), a 200-pt reservation must wait for the bucket to refill
    # from 100 — proving the header (not our estimate) governs the next call.
    cb.reserve(200)
    assert clock.t > 0.0


def test_sync_recent_empty_is_noop():
    client = FakeLinearClient(recent=[])
    conn = _RecordingConn()
    n = sync_recent(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        client=client,
        conn=conn,
        embedder=_FakeEmbedder(),
    )
    assert n == 0
    assert conn.inserts == []
