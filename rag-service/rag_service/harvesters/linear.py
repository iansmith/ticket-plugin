"""Linear upstream harvester (BILL-37).

Fetches tickets from the Linear GraphQL API, normalizes them into the
source-neutral `HarvestedTicket` shape, and feeds them through the shared
ingestion spine in `_common.py` (chunking, code/ticket-ref extraction,
embedding, full-resync DB write). Source-specific concerns live here; nothing
Linear-specific leaks into `_common.py` (so BILL-32's GitHub harvester reuses
the spine unchanged).

Two public sync entry points, matching the design's harvester interface
(`design/ticket-rag.md` §Ingestion → Upstream harvesters):

    sync_ticket(identifier, *, client, conn, embedder) -> int
    sync_recent(since, *, client, conn, embedder) -> int

Both take their collaborators by injection (a `LinearClient`, a psycopg
`Connection`, an `Embedder`) so unit tests drive them with a `FakeLinearClient`
+ `FakeEmbedder` + a recording fake connection — **zero live API calls, no
postgres, no model weights** (`design/rag-service-testing.md`). The `click` CLI
at the bottom is the only place that constructs the real collaborators.

Rate-limit budget (`design/ticket-rag.md` §Rate-limit budgets, verified
against https://linear.app/developers/rate-limiting on 2026-05-29). Linear's
API-key limits are **2,500 requests/hr AND 3,000,000 complexity-points/hr**,
with a **10,000-point single-query cap**, refilled via a leaky bucket. The
binding constraint flips by operation: a single-ticket fetch (~242 pts) is
request-bound, while a `sync_recent` page at `first: 40` (~9,660 pts) is
complexity-bound. So the client throttles on BOTH a `RateLimiter` (requests)
and a `ComplexityBudget` (points), and reconciles the point budget against the
server's `X-RateLimit-Complexity-Remaining` header after every call. A
throttled request is signalled by Linear as **HTTP 400 with GraphQL error code
`RATELIMITED`** (not 429); the client detects that specifically and backs off.

READ-ONLY: this harvester only ever issues GraphQL *queries* against Linear.
It never mutates LOU (or any) Linear workspace — see the ticket's "Out of
scope".
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Protocol

from rag_service.harvesters._common import (
    ComplexityBudget,
    HarvestedComment,
    HarvestedTicket,
    RateLimiter,
    chunk_ticket,
    embed_rows,
    write_ticket,
)

if TYPE_CHECKING:
    import httpx
    import psycopg

    from rag_service.embed import Embedder

SOURCE = "linear"
LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"
LINEAR_API_KEY_ENV = "LINEAR_API_KEY"

# Linear API-key rate-limit budget (design §Rate-limit budgets; source:
# linear.app/developers/rate-limiting). Two independent ceilings per hour plus a
# per-query cap; we model all three.
LINEAR_MAX_REQUESTS_PER_HOUR = 2500
LINEAR_MAX_COMPLEXITY_PER_HOUR = 3_000_000
LINEAR_MAX_QUERY_COMPLEXITY = 10_000
LINEAR_RATE_PERIOD_S = 3600.0

# Linear's complexity scoring: 0.1 pt/property, 1 pt/object, a connection
# multiplies its children by its `first:` argument (design §Rate-limit budgets).
_COMMENTS_PER_PAGE = 100  # the `comments(first: N)` in _TICKET_FIELDS
# Per-comment cost: 1 object (the comment) + {id, body, createdAt} = 3 props
# + nested user object (1) with {name} (1 prop) ≈ 1 + 0.3 + 1 + 0.1 = 2.4.
_COMMENT_COMPLEXITY = 2.4
# Per-issue scalar cost: the issue object (counted by the enclosing connection)
# carries {id, identifier, title, description, url} = 5 props = 0.5, and its
# comments connection contributes _COMMENTS_PER_PAGE * _COMMENT_COMPLEXITY.
_ISSUE_SCALAR_COMPLEXITY = 0.5

# Batch size for sync_recent. Capped so a single page stays under Linear's
# 10,000-pt per-query ceiling: each issue ≈ 0.5 + 100*2.4 ≈ 240.5 pts, so
# 10000 / 240.5 ≈ 41 issues is the hard ceiling; 40 leaves a small margin.
LINEAR_BATCH_SIZE = 40
_DEFAULT_MIN_INTERVAL_S = 1.0


def issue_complexity(comments_per_page: int = _COMMENTS_PER_PAGE) -> float:
    """Estimated Linear complexity points for one issue in our selection set.

    1 pt for the issue object is contributed by the enclosing `issues(first:)`
    connection multiplier, so here we sum only the issue's own scalar props plus
    its comments connection. Pure + exported so tests assert the math against
    Linear's documented scoring without hitting the API.
    """
    return _ISSUE_SCALAR_COMPLEXITY + comments_per_page * _COMMENT_COMPLEXITY


def page_complexity(num_issues: int, comments_per_page: int = _COMMENTS_PER_PAGE) -> float:
    """Estimated complexity for an `issues(first: num_issues)` page.

    The issues connection multiplies its children by `num_issues`; each child is
    1 (the issue object) + `issue_complexity()`.
    """
    return num_issues * (1.0 + issue_complexity(comments_per_page))


class LinearRateLimitError(RuntimeError):
    """Raised when Linear returns its `RATELIMITED` GraphQL error (HTTP 400).

    Distinct from a generic GraphQL error so callers can back off and retry
    rather than treating it as a hard failure.
    """

# Linear identifiers are `<TEAMKEY>-<number>` (e.g. LOU-102, MAZ-43). The team
# key is the part before the dash; for Linear it doubles as the cross-project
# prefix, so by default no explicit prefix→team map is needed.
_IDENTIFIER_RE = re.compile(r"^([A-Z][A-Z0-9]+)-(\d+)$")


def parse_identifier(identifier: str) -> tuple[str, int]:
    """Split `LOU-102` into ('LOU', 102). Raises ValueError on a malformed id."""
    m = _IDENTIFIER_RE.match(identifier.strip())
    if not m:
        raise ValueError(
            f"not a Linear identifier: {identifier!r} (expected <TEAMKEY>-<number>)"
        )
    return m.group(1), int(m.group(2))


def team_key_for(identifier: str, prefix_team_map: dict[str, str] | None = None) -> str:
    """Resolve the Linear team key for an identifier.

    For Linear the identifier prefix already IS the team key, so the default is
    the identity mapping. `prefix_team_map` (read from `.project-conf.toml`, or
    a future Linear `teams` API call) is an optional override for the rare case
    where a project's configured prefix differs from its Linear team key.
    """
    prefix, _ = parse_identifier(identifier)
    if prefix_team_map and prefix in prefix_team_map:
        return prefix_team_map[prefix]
    return prefix


def load_prefix_team_map(conf_path: str = ".project-conf.toml") -> dict[str, str]:
    """Build a prefix→team-key map from a Linear `.project-conf.toml`.

    A Linear conf carries a single `key` (the team key, which is also the
    identifier prefix), so this yields a one-entry identity map `{key: key}`.
    Returns an empty map if the file is missing or isn't a Linear project —
    callers then fall back to the identity resolution in `team_key_for`.
    """
    import tomllib

    try:
        with open(conf_path, "rb") as f:
            conf = tomllib.load(f)
    except FileNotFoundError:
        return {}
    if conf.get("system") != "linear":
        return {}
    key = conf.get("key")
    return {key: key} if key else {}


# ---------------------------------------------------------------------------
# Client protocol + real GraphQL implementation
# ---------------------------------------------------------------------------


class LinearClient(Protocol):
    """The surface `sync_ticket` / `sync_recent` depend on.

    Implemented for real by `LinearGraphQLClient` and as a canned-response fake
    in the unit tests. Both methods return source-neutral `HarvestedTicket`s;
    all Linear-payload mapping happens inside the implementation.
    """

    def fetch_ticket(self, identifier: str) -> HarvestedTicket | None:
        """Fetch one ticket by identifier (e.g. 'LOU-102'); None if not found."""
        ...

    def fetch_recent(self, since: datetime) -> list[HarvestedTicket]:
        """Fetch all tickets updated at/after `since`, paginated under budget."""
        ...


_TICKET_FIELDS = """
    id
    identifier
    title
    description
    url
    comments(first: 100) {
        nodes { id body createdAt user { name } }
    }
"""

_FETCH_TICKET_QUERY = (
    "query($team: String!, $number: Float!) {"
    "  issues(filter: { team: { key: { eq: $team } }, number: { eq: $number } }, first: 1) {"
    f"    nodes {{ {_TICKET_FIELDS} }}"
    "  }"
    "}"
)

_FETCH_RECENT_QUERY = (
    "query($since: DateTimeOrDuration!, $after: String, $first: Int!) {"
    "  issues(filter: { updatedAt: { gte: $since } }, first: $first, after: $after,"
    "         orderBy: updatedAt) {"
    "    pageInfo { hasNextPage endCursor }"
    f"    nodes {{ {_TICKET_FIELDS} }}"
    "  }"
    "}"
)


def _issue_to_harvested(node: dict) -> HarvestedTicket:
    """Map one Linear `Issue` GraphQL node into a HarvestedTicket."""
    comments = []
    for c in (node.get("comments") or {}).get("nodes", []) or []:
        comments.append(
            HarvestedComment(
                body=c.get("body") or "",
                author=(c.get("user") or {}).get("name"),
                created_at=_parse_dt(c.get("createdAt")),
                upstream_id=c.get("id"),
            )
        )
    return HarvestedTicket(
        source=SOURCE,
        ticket_id=node["identifier"],
        title=node.get("title") or "",
        description=node.get("description") or "",
        url=node.get("url"),
        comments=comments,
        raw_meta={"linear_id": node.get("id")},
    )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Linear returns RFC-3339 with a trailing 'Z'; fromisoformat wants +00:00
    # on Python < 3.11 (we run 3.12, but normalize anyway for safety).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class LinearGraphQLClient:
    """Real `LinearClient` backed by Linear's GraphQL API over httpx.

    Models Linear's real, dual-dimension budget (see module docstring):

      - `rate_limiter` (`RateLimiter`): the 2,500 requests/hr ceiling, plus a
        ~1 req/sec slow-walk. Binding for cheap single-ticket fetches.
      - `complexity_budget` (`ComplexityBudget`): the 3,000,000 points/hr
        ceiling. Binding for batched `sync_recent` pages. Before each call we
        `reserve()` the estimated cost; after each call we `observe()` the
        server's `X-RateLimit-Complexity-Remaining` header so estimation error
        can't drift us above the real remaining budget.

    A `RATELIMITED` response (HTTP 400) triggers exponential backoff up to
    `max_retries`, after which `LinearRateLimitError` propagates.

    Unit tests do NOT construct this — they pass a FakeLinearClient. It exists
    to make the live path real; the CLI is its only production caller.
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = LINEAR_GRAPHQL_ENDPOINT,
        rate_limiter: RateLimiter | None = None,
        complexity_budget: ComplexityBudget | None = None,
        http_client: httpx.Client | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        import httpx

        self._endpoint = endpoint
        self._rate_limiter = rate_limiter or RateLimiter(
            max_calls=LINEAR_MAX_REQUESTS_PER_HOUR,
            period_s=LINEAR_RATE_PERIOD_S,
            min_interval_s=_DEFAULT_MIN_INTERVAL_S,
        )
        self._complexity = complexity_budget or ComplexityBudget(
            max_points=LINEAR_MAX_COMPLEXITY_PER_HOUR,
            period_s=LINEAR_RATE_PERIOD_S,
        )
        self._max_retries = max_retries
        self._sleep = sleep
        # Linear authenticates with the raw API key in the Authorization header
        # (no "Bearer " prefix for personal API keys).
        self._http = http_client or httpx.Client(
            base_url=endpoint,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    @staticmethod
    def _is_ratelimited(payload: dict) -> bool:
        """True iff the GraphQL error body carries Linear's RATELIMITED code."""
        for err in payload.get("errors") or []:
            ext = err.get("extensions") or {}
            if ext.get("code") == "RATELIMITED" or err.get("code") == "RATELIMITED":
                return True
        return False

    def _post(self, query: str, variables: dict, *, est_cost: float) -> dict:
        """Issue one GraphQL request under both budgets; return `data`.

        `est_cost` is the caller's complexity estimate, reserved against the
        point budget ONCE per logical request. On a `RATELIMITED` response we
        back off (exponential) and retry up to `max_retries`; any other GraphQL
        error raises immediately. The complexity reservation is NOT repeated on
        retries — a throttled attempt incurred no server-side query cost, so
        re-charging the local budget each retry would over-throttle (up to
        `max_retries+1`× the real cost). The request-count `RateLimiter`, by
        contrast, IS acquired each attempt — every retry is a real HTTP call.
        """
        attempt = 0
        reserved = False
        while True:
            self._rate_limiter.acquire()
            if not reserved:
                self._complexity.reserve(est_cost)
                reserved = True
            resp = self._http.post(
                self._endpoint, json={"query": query, "variables": variables}
            )

            # Reconcile with the server's authoritative remaining-points header
            # regardless of status, so our local bucket tracks reality.
            remaining = resp.headers.get("X-RateLimit-Complexity-Remaining")
            if remaining is not None:
                try:
                    self._complexity.observe(float(remaining))
                except ValueError:
                    pass  # malformed header — keep our local estimate

            # Linear signals throttling as HTTP 400 + RATELIMITED in the body,
            # NOT 429. Detect it before the generic raise_for_status path.
            payload = resp.json() if resp.content else {}
            if resp.status_code == 400 and self._is_ratelimited(payload):
                attempt += 1
                if attempt > self._max_retries:
                    raise LinearRateLimitError(
                        f"Linear RATELIMITED after {self._max_retries} retries"
                    )
                # Exponential backoff: 1, 2, 4, ... seconds.
                self._sleep(2 ** (attempt - 1))
                continue

            resp.raise_for_status()
            if payload.get("errors"):
                raise RuntimeError(f"Linear GraphQL errors: {payload['errors']}")
            return payload["data"]

    def fetch_ticket(self, identifier: str) -> HarvestedTicket | None:
        team, number = parse_identifier(identifier)
        data = self._post(
            _FETCH_TICKET_QUERY,
            {"team": team, "number": float(number)},
            est_cost=page_complexity(1),
        )
        nodes = data["issues"]["nodes"]
        return _issue_to_harvested(nodes[0]) if nodes else None

    def fetch_recent(self, since: datetime) -> list[HarvestedTicket]:
        out: list[HarvestedTicket] = []
        after: str | None = None
        while True:
            data = self._post(
                _FETCH_RECENT_QUERY,
                {"since": since.isoformat(), "after": after, "first": LINEAR_BATCH_SIZE},
                est_cost=page_complexity(LINEAR_BATCH_SIZE),
            )
            issues = data["issues"]
            out.extend(_issue_to_harvested(n) for n in issues["nodes"])
            page = issues["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        return out


# ---------------------------------------------------------------------------
# Sync orchestration (injection-driven; unit-tested with fakes)
# ---------------------------------------------------------------------------


def _ingest(
    ticket: HarvestedTicket,
    *,
    conn: psycopg.Connection,
    embedder: Embedder,
) -> int:
    """Chunk → embed → full-resync write for one ticket. Returns rows written."""
    rows = chunk_ticket(ticket)
    embed_rows(rows, embedder)
    return write_ticket(
        conn, rows, source=ticket.source, ticket_id=ticket.ticket_id
    )


def sync_ticket(
    identifier: str,
    *,
    client: LinearClient,
    conn: psycopg.Connection,
    embedder: Embedder,
) -> int:
    """Full re-fetch + replace for one Linear ticket. Returns rows written.

    Returns 0 (a no-op) if the ticket can't be found upstream — a deleted or
    inaccessible ticket is not an error here; the harvester just has nothing to
    index. (Deletion *cleanup* of previously-indexed rows is the harvester
    pass's job via `sync_recent`, not a single-ticket fetch.)
    """
    ticket = client.fetch_ticket(identifier)
    if ticket is None:
        return 0
    return _ingest(ticket, conn=conn, embedder=embedder)


def sync_recent(
    since: datetime,
    *,
    client: LinearClient,
    conn: psycopg.Connection,
    embedder: Embedder,
) -> int:
    """Batch catch-up: re-index every ticket updated at/after `since`.

    Returns the total number of chunk rows written across all tickets. The
    client owns pagination + rate limiting (≤LINEAR_BATCH_SIZE tickets/request,
    under the complexity-point budget — see the module docstring); this
    orchestration just ingests each returned ticket.
    """
    total = 0
    for ticket in client.fetch_recent(since):
        total += _ingest(ticket, conn=conn, embedder=embedder)
    return total


# ---------------------------------------------------------------------------
# CLI (the only place real collaborators are constructed)
# ---------------------------------------------------------------------------


def _build_real_client() -> LinearGraphQLClient:
    api_key = os.environ.get(LINEAR_API_KEY_ENV)
    if not api_key:
        raise SystemExit(
            f"{LINEAR_API_KEY_ENV} is not set — export a Linear API key to run "
            "the harvester."
        )
    return LinearGraphQLClient(api_key)


def _open_conn() -> psycopg.Connection:
    import psycopg

    from rag_service.db import PG_DSN

    return psycopg.connect(PG_DSN)


try:  # `click` is a runtime dep; guard the import so unit tests that only call
    import click  # the sync functions don't require the CLI layer to import.
except ImportError:  # pragma: no cover
    click = None  # type: ignore[assignment]


if click is not None:

    @click.group()
    def cli() -> None:
        """Linear harvester for the ticket-rag service."""

    @cli.command("sync-ticket")
    @click.argument("identifier")
    def sync_ticket_cmd(identifier: str) -> None:
        """Re-index a single Linear ticket, e.g. `sync-ticket LOU-102`."""
        from rag_service.embed import get_embedder

        client = _build_real_client()
        conn = _open_conn()
        try:
            n = sync_ticket(
                identifier, client=client, conn=conn, embedder=get_embedder()
            )
        finally:
            conn.close()
        click.echo(f"{identifier}: wrote {n} chunk row(s)")

    @cli.command("sync-recent")
    @click.argument("since")
    def sync_recent_cmd(since: str) -> None:
        """Re-index every Linear ticket updated at/after an ISO-8601 timestamp."""
        from rag_service.embed import get_embedder

        client = _build_real_client()
        conn = _open_conn()
        try:
            n = sync_recent(
                datetime.fromisoformat(since.replace("Z", "+00:00")),
                client=client,
                conn=conn,
                embedder=get_embedder(),
            )
        finally:
            conn.close()
        click.echo(f"since {since}: wrote {n} chunk row(s)")

    if __name__ == "__main__":  # pragma: no cover
        cli()
