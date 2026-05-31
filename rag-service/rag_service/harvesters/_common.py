"""Harvester-agnostic ingestion spine shared by every upstream harvester.

Built first by BILL-37 (the Linear harvester); BILL-32 (GitHub) and any future
JIRA harvester import these symbols rather than re-implementing chunking,
code/ticket-ref extraction, embedding, or the full-resync DB write. Per the
sequencing note in #37/#32: whichever harvester lands first owns this module.

The pipeline, in order:

    HarvestedTicket            # source-neutral, produced by each harvester
        -> chunk_ticket()      # one ChunkRow per logical unit (description,
                               #   each comment), with code fences stripped and
                               #   code_refs / ticket_refs extracted (PURE)
        -> embed_rows()        # fill ChunkRow.embedding via Embedder (thin)
        -> write_ticket()      # BEGIN; DELETE upstream rows; INSERT; COMMIT (I/O)

Everything above `write_ticket` is pure or near-pure and unit-tested directly
at Layer 1 (no postgres, no model weights, no network) per
`design/rag-service-testing.md`. `write_ticket` touches pgvector and is
exercised only by the Docker integration gate (`verify-bill37.sh`).

Design references: `design/ticket-rag.md` §Chunking strategy, §Code blocks,
§Cross-ticket reference extraction, §Ingestion (full re-sync), §Rate-limit
budgets. Schema: `docker/postgres-pgvector/schema/001_ticket_chunks.sql`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from datetime import datetime

    import psycopg

    from rag_service.embed import Embedder


# ---------------------------------------------------------------------------
# Source-neutral input shape
# ---------------------------------------------------------------------------


@dataclass
class HarvestedComment:
    """One comment on a ticket, normalized across source systems."""

    body: str
    author: str | None = None
    created_at: datetime | None = None
    upstream_id: str | None = None  # source-system comment ID, if any


@dataclass
class HarvestedTicket:
    """A ticket fetched from an upstream system, normalized.

    Each harvester maps its API payload into this shape; everything downstream
    is source-agnostic. `source` is the `ticket_chunks.source` value
    ('linear' | 'jira' | 'github'); `ticket_id` is the system-qualified
    identifier ('LOU-102', 'PLTF-12', 'iansmith/slopstop#7').
    """

    source: str
    ticket_id: str
    title: str
    description: str
    url: str | None = None
    comments: list[HarvestedComment] = field(default_factory=list)
    raw_meta: dict | None = None


# ---------------------------------------------------------------------------
# Assembled output shape (one row per ticket_chunks row to be written)
# ---------------------------------------------------------------------------


@dataclass
class ChunkRow:
    """One `ticket_chunks` row, assembled but not yet embedded or persisted.

    `text` is the exact string that gets embedded AND stored in
    `ticket_chunks.text` (the schema's "exact text that was embedded"
    invariant) — code fences already stripped, the synthesized code-reference
    sentence already appended. `embedding` is filled in later by `embed_rows`.
    """

    source: str
    ticket_id: str
    provenance: str  # 'upstream' for harvester rows
    kind: str  # 'description' | 'comment'
    seq: int  # description band 0..; comment band COMMENT_SEQ_BASE+i
    text: str
    code_refs: list[dict]
    ticket_refs: list[str]
    upstream_id: str | None = None
    author: str | None = None
    created_at: datetime | None = None
    raw_meta: dict | None = None
    embedding: list[float] | None = None  # filled by embed_rows()


# ---------------------------------------------------------------------------
# Code-block handling (design §"Code blocks: signal, not text")
# ---------------------------------------------------------------------------

# Fenced code blocks: ``` or ~~~ (3+ markers), closing fence must match the
# opener's character. DOTALL so the body spans lines; MULTILINE so ^/$ anchor
# per line. The (?P=fence) backreference forces a same-character close, so a
# ``` block containing ~~~ text isn't truncated early.
_FENCE_RE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

# A file path: optional dir segments, then name.ext where ext STARTS WITH a
# letter. The letter-led extension is deliberate — it rejects version numbers
# like "2.8.0" (ext "0") and "v1.2" while accepting "sched.go", "a/b/foo.py".
_FILE_RE = re.compile(r"(?:[\w.-]+/)*[\w-]+\.[A-Za-z][A-Za-z0-9]*")

# Function declarations across the common languages we see in tickets.
_FUNC_DECL_RE = re.compile(
    r"\b(?:def|func|function|fn|sub)\s+([A-Za-z_]\w*)",
)
# Function/method CALL sites: name immediately followed by '('. Broad on
# purpose; filtered against language keywords below.
_FUNC_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

# Tokens that match _FUNC_CALL_RE but are control-flow / keywords, not funcs.
_NON_FUNC_TOKENS = frozenset(
    {
        "if", "for", "while", "switch", "return", "with", "match", "case",
        "catch", "elif", "else", "and", "or", "not", "in", "is", "def",
        "func", "function", "fn", "sub", "import", "from", "print", "assert",
        "await", "yield", "raise", "throw", "new", "del", "lambda", "class",
        "struct", "enum", "type", "var", "let", "const", "do", "try",
    }
)


def strip_code_blocks(text: str) -> tuple[str, list[str]]:
    """Split `text` into (prose with code blocks removed, list of block bodies).

    Fenced blocks (``` / ~~~) are removed from the prose so the embedding sees
    natural language only — transformer encoders trained on prose are confused
    by literal diff syntax (`---`, `+++`, `@@`). The removed block bodies are
    returned for `extract_code_refs` to mine for file/function/module signal.

    Leaves inline `code` spans untouched: they're usually a single identifier
    inside a sentence and carry prose-level signal.
    """
    blocks: list[str] = []

    def _collect(m: re.Match[str]) -> str:
        blocks.append(m.group("body"))
        return ""

    stripped = _FENCE_RE.sub(_collect, text)
    # Collapse the blank-line holes left by removed fences.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped, blocks


def _module_of(file_path: str) -> str:
    """Top-level module/package for a file path.

    `kmazarin/sched.go` -> `kmazarin`; `foo.py` -> `foo` (the stem). Mirrors the
    design's example where module is the leading package directory.
    """
    if "/" in file_path:
        return file_path.split("/", 1)[0]
    return file_path.rsplit(".", 1)[0]


def extract_code_refs(code_blocks: list[str]) -> list[dict]:
    """Mine code blocks for structured `[{file, func, module}, ...]` refs.

    Heuristic and deliberately **line-number-free** (design §Code blocks): line
    numbers go stale on the next commit; file/function/module identifiers are
    stable enough to keep. We do NOT attempt to pair a function back to the
    file it was defined in — the block structure needed for that is exactly the
    line-level detail we discard — so files and functions are emitted as
    separate ref entries:

        - one `{"file": ..., "module": ...}` entry per unique file path
        - one `{"func": ...}` entry per unique function name

    Module is derived from the file path (the leading package directory, per
    the design's `kmazarin/sched.go` -> `kmazarin` example). We do NOT parse
    `import` statements for module names: distinguishing a dotted module path
    (`rag_service.embed`) from a real filename (`sched.go`) or a version
    string (`2.8.0`) heuristically is ambiguous, and the design only calls for
    file/func/module-from-path. Import-derived modules are an out-of-scope
    refinement.

    Order is first-seen within the concatenated blocks, so output is
    deterministic for a given input (unit-testable).
    """
    files: list[str] = []
    funcs: list[str] = []
    seen_files: set[str] = set()
    seen_funcs: set[str] = set()

    for block in code_blocks:
        for m in _FILE_RE.finditer(block):
            path = m.group(0)
            if path not in seen_files:
                seen_files.add(path)
                files.append(path)
        for rx in (_FUNC_DECL_RE, _FUNC_CALL_RE):
            for m in rx.finditer(block):
                name = m.group(1)
                if name in _NON_FUNC_TOKENS or name in seen_funcs:
                    continue
                seen_funcs.add(name)
                funcs.append(name)

    refs: list[dict] = []
    for f in files:
        refs.append({"file": f, "module": _module_of(f)})
    for fn in funcs:
        refs.append({"func": fn})
    return refs


def synthesize_code_sentence(code_refs: list[dict]) -> str:
    """One English sentence describing the code refs, appended before embedding.

    Example (design §Code blocks):
        "This text references function `runqGet` in file `kmazarin/sched.go`."

    Functions are described as being "in" the files/modules, so the category
    clauses are joined with " in " (a function lives in a file). Returns "" when
    there are no refs (nothing to append). Keeps the transformer's input as
    natural language while preserving the code signal stripped out of the prose.
    """
    files = [r["file"] for r in code_refs if "file" in r]
    funcs = [r["func"] for r in code_refs if "func" in r]
    # Standalone modules (a module ref carrying no file). With import parsing
    # out of scope, every module currently rides along with a file, so this is
    # normally empty — kept so a future import-aware extractor degrades cleanly.
    modules = [r["module"] for r in code_refs if "module" in r and "file" not in r]

    clauses: list[str] = []
    if funcs:
        noun = "function" if len(funcs) == 1 else "functions"
        clauses.append(f"{noun} {_join_backticked(funcs)}")
    if files:
        noun = "file" if len(files) == 1 else "files"
        clauses.append(f"{noun} {_join_backticked(files)}")
    if modules:
        noun = "module" if len(modules) == 1 else "modules"
        clauses.append(f"{noun} {_join_backticked(modules)}")

    if not clauses:
        return ""
    return "This text references " + " in ".join(clauses) + "."


def _join_backticked(items: list[str]) -> str:
    ticked = [f"`{i}`" for i in items]
    return _join_clauses(ticked)


def _join_clauses(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# ---------------------------------------------------------------------------
# Cross-ticket reference extraction (design §"Cross-ticket reference extraction")
# ---------------------------------------------------------------------------

# Prefixed Linear/JIRA IDs: MAZ-43, PLTF-12, LOU-102. Prefix is 2+ uppercase
# letters/digits starting with a letter.
_PREFIXED_REF_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
# GitHub owner/repo#N (fully qualified).
_GH_FULL_REF_RE = re.compile(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)\b")
# GitHub bare #N — only when NOT preceded by a repo path char (so it doesn't
# double-count the '#N' tail of an owner/repo#N already matched above).
_GH_BARE_REF_RE = re.compile(r"(?<![\w/])#(\d+)\b")


def extract_ticket_refs(text: str) -> list[str]:
    """Canonical cross-ticket references mentioned in `text`, deduped + sorted.

    Recognizes:
      - prefixed IDs: `MAZ-15`, `PLTF-12`, `LOU-94`  -> kept verbatim
      - GitHub fully-qualified: `iansmith/mazzy#42`   -> kept verbatim
      - GitHub bare: `#42`                            -> kept as `#42`

    Sorted for deterministic output (the order tickets are mentioned carries no
    meaning, and a stable order makes the JSONB column and its tests stable).
    """
    refs: set[str] = set()
    refs.update(_PREFIXED_REF_RE.findall(text))
    full = _GH_FULL_REF_RE.findall(text)
    refs.update(full)
    # Bare #N: include only those not already covered by an owner/repo#N match.
    full_tails = {f.split("#")[-1] for f in full}
    for n in _GH_BARE_REF_RE.findall(text):
        if n not in full_tails:
            refs.add(f"#{n}")
    return sorted(refs)


# ---------------------------------------------------------------------------
# Chunking (design §"Chunking strategy")
# ---------------------------------------------------------------------------

# Seq-allocation scheme. The schema's UNIQUE(source,ticket_id,provenance,kind,
# seq) plus the design's "seq=0 for description" means description and comment
# seq spaces must not collide. We carve seq into two bands:
#
#   - Description chunks: seq 0 .. COMMENT_SEQ_BASE-1 (0x000..0xFFF).
#     Normally just seq=0; a description split into heading-anchored 512-token
#     sections (design §Chunking) fans out into seq=0,1,2,...
#   - Comments: seq COMMENT_SEQ_BASE + i (0x1000, 0x1001, ...).
#
# A 4096-slot description band is absurdly generous — no real ticket
# description splits into thousands of paragraphs — but the explicit gap keeps
# the two kinds' seq spaces provably disjoint without a second column.
COMMENT_SEQ_BASE = 0x1000  # 4096

# Hard token cap for EVERY chunk — description, comment, or finding (BILL-43).
# 512 is the reranker's `max_length` (rerank.py); a chunk at or under it is the
# only setting where the cross-encoder scores the chunk IN FULL with no
# truncation, and where bge-m3's averaged vector stays sharp. The cap is
# enforced with the reranker's REAL tokenizer (not chars/4), because measured
# ticket text runs ~2.5 chars/token — chars/4 under-counts ~1.6×, so a chunk
# that looks like 512 "tokens" by the estimate can really be ~800 and get
# truncated, defeating the whole point. See `design/ticket-rag.md` §Chunking.
MAX_CHUNK_TOKENS = 512

# Markdown section headings we anchor chunks on: `##` or `###` (h2/h3). h1 is
# rare in ticket bodies and usually the title (already folded in elsewhere).
_HEADING_RE = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Paragraph separator: one or more blank lines (allowing trailing whitespace).
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n+")


def _real_token_counter(text: str) -> int:
    """Exact token count using the reranker's own tokenizer (BILL-43).

    The production default for `chunk_text`: sizing chunks against the SAME
    tokenizer the reranker scores with is what guarantees no chunk is ever
    truncated by the reranker's `max_length` window. Special tokens are excluded
    — we count the chunk's own content tokens, not the (query, passage) framing
    the reranker adds at score time. Lazy: the tokenizer loads only in the
    harvest process, never at import or in pytest (tests inject a fake counter).
    """
    from rag_service.rerank import get_reranker_tokenizer

    return len(get_reranker_tokenizer().encode(text, add_special_tokens=False))


# Module default counter for the heading-aware chunker. Injected as a kwarg so
# tests pass a weightless fake; production uses the real reranker tokenizer.
_DEFAULT_TOKEN_COUNTER: Callable[[str], int] = _real_token_counter


def _split_into_units(text: str) -> list[str]:
    """Split `text` into atomic units: paragraphs and whole fenced code blocks.

    A fenced code block is kept intact as one unit (never split mid-fence —
    splitting a diff in half is exactly what we must avoid); the prose between
    fences is split on blank lines into paragraph units. Empty units dropped.
    Order is preserved, so re-joining the units with blank lines reconstructs
    the original modulo whitespace normalization.
    """
    units: list[str] = []
    last = 0
    for m in _FENCE_RE.finditer(text):
        pre = text[last : m.start()]
        units.extend(p.strip() for p in _PARAGRAPH_SPLIT_RE.split(pre) if p.strip())
        units.append(m.group(0).strip())  # the whole fence, markers included
        last = m.end()
    tail = text[last:]
    units.extend(p.strip() for p in _PARAGRAPH_SPLIT_RE.split(tail) if p.strip())
    return units


def _is_code_fence(unit: str) -> bool:
    """True if `unit` is a whole fenced code block (``` or ~~~ opener).

    Fences are NEVER word-split — cutting a diff or code listing in half is
    exactly the failure the unit-splitter exists to avoid. An oversized fence
    stays one chunk and the encoder truncates it; a half-fence is worse.
    """
    return unit.lstrip().startswith(("```", "~~~"))


def _split_oversized_unit(
    unit: str, max_tokens: int, token_counter: Callable[[str], int]
) -> list[str]:
    """Split a single prose unit that alone exceeds `max_tokens` on word
    boundaries into pieces that each fit the budget.

    Most ticket bodies are one long unsectioned paragraph, so paragraph-level
    splitting isn't fine enough to honor a hard 512-token cap — a single
    paragraph can blow the budget by itself. Code fences are returned whole
    (see `_is_code_fence`); a unit already within budget is returned unchanged.
    """
    if _is_code_fence(unit) or token_counter(unit) <= max_tokens:
        return [unit]
    pieces: list[str] = []
    current: list[str] = []
    for word in unit.split():
        if current and token_counter(" ".join([*current, word])) > max_tokens:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _pack_units(
    units: list[str],
    max_tokens: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    """Greedily pack atomic units into chunks of at most `max_tokens` tokens.

    A prose unit larger than the budget is first split on word boundaries
    (`_split_oversized_unit`) so the hard cap is honored; a code fence that
    overshoots stays whole (never split mid-fence — design §Chunking). No
    overlap between chunks: recent practice showed chunk overlap doesn't improve
    retrieval here and only inflates the index, so each unit lands in exactly
    one chunk. `token_counter` is injected (the reranker's real tokenizer in
    production, a weightless fake in tests) — there is no chars/4 fallback.
    """
    expanded: list[str] = []
    for unit in units:
        expanded.extend(_split_oversized_unit(unit, max_tokens, token_counter))

    chunks: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for unit in expanded:
        unit_tokens = token_counter(unit)
        if current and current_tokens + unit_tokens > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit_tokens

    if current:
        chunks.append(current)
    return ["\n\n".join(c) for c in chunks]


def _split_into_sections(text: str) -> list[tuple[str | None, str]]:
    """Split `text` into `(heading, body)` sections on `##`/`###` headings.

    The heading is the full markdown heading line (e.g. `## Context`), kept so
    it can be re-prefixed onto every chunk derived from the section. Text before
    the first heading — or all of `text` when there are no headings, the common
    case for ticket descriptions and comments — is one section with heading
    `None`. Empty bodies are preserved as `(heading, "")` so a bare heading
    still produces a chunk; an all-blank input yields no sections.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [(None, stripped)] if stripped else []

    sections: list[tuple[str | None, str]] = []
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))
    for i, m in enumerate(matches):
        heading = m.group(0).strip()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : body_end].strip()
        sections.append((heading, body))
    return sections


def chunk_text(
    text: str,
    *,
    token_counter: Callable[[str], int] = _DEFAULT_TOKEN_COUNTER,
    max_tokens: int = MAX_CHUNK_TOKENS,
) -> list[str]:
    """Heading-anchored, token-budgeted splitter shared by descriptions,
    comments, and findings (BILL-43; design §Chunking).

    Walks `##`/`###` sections; each section is the base logical unit. Every
    chunk is PREFIXED with its section heading so a sub-split fragment still
    carries "what is this about". A section over `max_tokens` is split on
    paragraph then word boundaries (code fences kept intact), each piece
    re-prefixed with the heading, NO overlap. The heading's own tokens count
    against the budget, so the body is packed against `max_tokens` minus the
    prefix cost — the final prefixed chunk stays within the cap.

    `token_counter` is injected: the reranker's real tokenizer in production
    (the only count that guarantees no reranker truncation), a weightless fake
    in tests. Returns the list of chunk strings in document order.
    """
    chunks: list[str] = []
    for heading, body in _split_into_sections(text):
        prefix = f"{heading}\n\n" if heading else ""
        if not body:
            if heading:
                chunks.append(heading)
            continue
        combined = f"{prefix}{body}"
        if token_counter(combined) <= max_tokens:
            chunks.append(combined)
            continue
        body_budget = max(1, max_tokens - (token_counter(prefix) if prefix else 0))
        for piece in _pack_units(_split_into_units(body), body_budget, token_counter):
            chunks.append(f"{prefix}{piece}")
    return chunks


def _assemble_text(raw: str) -> tuple[str, list[dict], list[str]]:
    """Shared per-chunk transform: strip code, extract refs, append the
    synthesized code sentence. Returns (embed_text, code_refs, ticket_refs).

    Ticket refs are extracted from the ORIGINAL raw text (a ticket ID mentioned
    inside a code block still counts as a reference), while code refs come from
    the stripped blocks and the embed text is the prose-only body plus the
    synthesized sentence.
    """
    prose, blocks = strip_code_blocks(raw)
    code_refs = extract_code_refs(blocks)
    ticket_refs = extract_ticket_refs(raw)
    sentence = synthesize_code_sentence(code_refs)
    embed_text = f"{prose}\n\n{sentence}".strip() if sentence else prose
    return embed_text, code_refs, ticket_refs


def chunk_ticket(
    ticket: HarvestedTicket,
    *,
    provenance: str = "upstream",
    token_counter: Callable[[str], int] = _DEFAULT_TOKEN_COUNTER,
) -> list[ChunkRow]:
    """Split a HarvestedTicket into ChunkRows via the heading-aware chunker.

    Both the description AND every comment run through `chunk_text` (BILL-43):
    heading-anchored `##`/`###` sections, each chunk prefixed with its heading,
    each ≤MAX_CHUNK_TOKENS real tokens, oversized sections split on paragraph
    then word boundaries with NO overlap. `token_counter` is injected so the
    production path counts with the reranker's real tokenizer (the only count
    that guarantees no reranker truncation) while tests pass a weightless fake.

      - Description -> kind='description', seq 0.. (band 0..COMMENT_SEQ_BASE-1).
        The first chunk leads with `title` (short, high-signal — gives an
        otherwise-terse description retrievable context); the title rides the
        first section as preamble.
      - Comments -> kind='comment', seq COMMENT_SEQ_BASE.. as ONE running
        counter across ALL comments' sub-chunks. This is the BILL-43 reversal of
        the old "each comment is one logical unit, never split" rule: a comment
        over the token cap now fans out into multiple heading-prefixed chunks,
        each carrying that comment's author/created_at/upstream_id.

    The two seq bands keep description and comment seqs provably disjoint under
    the schema's UNIQUE(source,ticket_id,provenance,kind,seq), without a second
    column (see COMMENT_SEQ_BASE).
    """
    rows: list[ChunkRow] = []

    title = ticket.title.strip()
    description = ticket.description.strip()
    desc_raw = f"{title}\n\n{description}" if description else title

    desc_pieces = chunk_text(desc_raw, token_counter=token_counter)

    if len(desc_pieces) > COMMENT_SEQ_BASE:
        # Pathological: a description that splits into 4096+ pieces would spill
        # into the comment seq band. Fail loud rather than corrupt the index.
        raise ValueError(
            f"description for {ticket.ticket_id} split into {len(desc_pieces)} "
            f"chunks, exceeding the {COMMENT_SEQ_BASE}-slot description seq band"
        )

    for seq, piece in enumerate(desc_pieces):
        embed_text, code_refs, ticket_refs = _assemble_text(piece)
        rows.append(
            ChunkRow(
                source=ticket.source,
                ticket_id=ticket.ticket_id,
                provenance=provenance,
                kind="description",
                seq=seq,
                text=embed_text,
                code_refs=code_refs,
                ticket_refs=ticket_refs,
                # raw_meta belongs to the ticket as a whole; attach it to the
                # first description chunk only so it isn't duplicated N times.
                raw_meta=ticket.raw_meta if seq == 0 else None,
            )
        )

    # One running counter across ALL comments' sub-chunks, so the comment band
    # is contiguous (COMMENT_SEQ_BASE, +1, +2, ...) no matter how many chunks
    # each individual comment fans out into.
    comment_seq = COMMENT_SEQ_BASE
    for comment in ticket.comments:
        if not comment.body.strip():
            continue  # empty comment carries no signal; skip it
        for piece in chunk_text(comment.body, token_counter=token_counter):
            embed_text, code_refs, ticket_refs = _assemble_text(piece)
            rows.append(
                ChunkRow(
                    source=ticket.source,
                    ticket_id=ticket.ticket_id,
                    provenance=provenance,
                    kind="comment",
                    seq=comment_seq,
                    text=embed_text,
                    code_refs=code_refs,
                    ticket_refs=ticket_refs,
                    # Comment-level metadata rides every sub-chunk of that
                    # comment, so each piece is independently attributable.
                    upstream_id=comment.upstream_id,
                    author=comment.author,
                    created_at=comment.created_at,
                )
            )
            comment_seq += 1

    return rows


# ---------------------------------------------------------------------------
# Embedding (thin wrapper over the injected Embedder)
# ---------------------------------------------------------------------------


def embed_rows(rows: list[ChunkRow], embedder: Embedder) -> list[ChunkRow]:
    """Fill each row's `embedding` from its `text` via the encoder.

    Mutates and returns `rows`. `encode_passage` returns a numpy array; we
    store it as a plain list (psycopg has no numpy adapter — the `::vector`
    cast in `write_ticket` turns the bound list into a pgvector value, exactly
    as `db.knn_search` does on the read side).

    The embedder is injected so unit tests pass `FakeEmbedder` and never load
    real model weights (`design/rag-service-testing.md`).
    """
    for row in rows:
        row.embedding = embedder.encode_passage(row.text).tolist()
    return rows


# ---------------------------------------------------------------------------
# Persistence — full re-sync per ticket (design §Ingestion)
# ---------------------------------------------------------------------------

_INSERT_COLUMNS = (
    "source",
    "ticket_id",
    "provenance",
    "kind",
    "seq",
    "upstream_id",
    "author",
    "created_at",
    "text",
    "embedding",
    "code_refs",
    "ticket_refs",
    "raw_meta",
)


def write_ticket(
    conn: psycopg.Connection,
    rows: list[ChunkRow],
    *,
    source: str,
    ticket_id: str,
    provenance: str = "upstream",
) -> int:
    """Atomically replace all `provenance` rows for one ticket (design §Ingestion).

        BEGIN;
        DELETE FROM ticket_chunks
         WHERE source=? AND ticket_id=? AND provenance=?;
        INSERT INTO ticket_chunks (...) VALUES (...);   -- one per row
        COMMIT;

    This is the only correct deletion semantics: a comment deleted upstream
    vanishes from the index on the next re-sync, an edited comment's old row is
    replaced — no tombstones, no soft-delete. The delete is scoped to
    `provenance` so an upstream re-sync never touches `provenance='local'` rows
    (and vice-versa).

    Returns the number of rows inserted. `rows` must already be embedded
    (`embed_rows`); a row with `embedding is None` is a programming error and
    raises. Integration-tested via the Docker gate — pgvector can't run under
    SQLite, so there is no Layer-1 test for this function.
    """
    from psycopg.types.json import Jsonb

    for row in rows:
        if row.embedding is None:
            raise ValueError(
                f"write_ticket got an un-embedded row (seq={row.seq}); "
                "call embed_rows() before write_ticket()"
            )
        if (
            row.source != source
            or row.ticket_id != ticket_id
            or row.provenance != provenance
        ):
            raise ValueError(
                "write_ticket got a row whose identity is outside the resync "
                f"scope: expected ({source}, {ticket_id}, {provenance}) but got "
                f"({row.source}, {row.ticket_id}, {row.provenance}). The DELETE "
                "is scoped by the function args, so a row from a different scope "
                "would be inserted without its own scope being cleared first — "
                "leaving stale rows behind."
            )

    cols = ", ".join(_INSERT_COLUMNS)
    placeholders = ", ".join(
        "%s::vector" if c == "embedding" else "%s" for c in _INSERT_COLUMNS
    )
    insert_sql = f"INSERT INTO ticket_chunks ({cols}) VALUES ({placeholders})"

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ticket_chunks "
                "WHERE source = %s AND ticket_id = %s AND provenance = %s",
                (source, ticket_id, provenance),
            )
            for row in rows:
                cur.execute(
                    insert_sql,
                    (
                        # Bind the resync-scope args (not row.* identity) so an
                        # inserted row can never fall outside the DELETE scope;
                        # the guard above already proved they're equal.
                        source,
                        ticket_id,
                        provenance,
                        row.kind,
                        row.seq,
                        row.upstream_id,
                        row.author,
                        row.created_at,
                        row.text,
                        row.embedding,
                        Jsonb(row.code_refs) if row.code_refs else None,
                        Jsonb(row.ticket_refs) if row.ticket_refs else None,
                        Jsonb(row.raw_meta) if row.raw_meta else None,
                    ),
                )
    return len(rows)


# ---------------------------------------------------------------------------
# Rate limiting (design §"Rate-limit budgets")
# ---------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window *request-count* limiter with an optional inter-call gap.

    Models the request-count dimension of an API budget — for Linear, the
    2,500 req/hr API-key ceiling (design §Rate-limit budgets). This is the
    binding constraint for cheap single-ticket fetches; for batched sweeps the
    binding constraint is complexity points, modelled separately by
    `ComplexityBudget`. The Linear client uses both together. Construct as
    `RateLimiter(max_calls=2500, period_s=3600)` and `acquire()` before each
    request.

    `acquire()` blocks (via the injected `sleep`) only as long as needed to
    respect either constraint, then records the call. The `clock`/`sleep`
    callables are injected so unit tests can drive virtual time with zero real
    waiting and assert exactly when throttling would occur — no wall-clock
    flakiness, and no dependence on `time` at import.
    """

    def __init__(
        self,
        max_calls: int,
        period_s: float,
        *,
        min_interval_s: float = 0.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        import time as _time

        self.max_calls = max_calls
        self.period_s = period_s
        self.min_interval_s = min_interval_s
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep
        self._calls: list[float] = []  # timestamps of recorded acquisitions

    def acquire(self) -> None:
        """Block until a call is permitted under both constraints, then record it."""
        # Minimum inter-call spacing (the "slow walk" default of ~1 req/sec).
        if self.min_interval_s and self._calls:
            gap = self.min_interval_s - (self._clock() - self._calls[-1])
            if gap > 0:
                self._sleep(gap)

        # Sliding window: if the window is full, wait until its oldest call ages out.
        self._evict()
        if len(self._calls) >= self.max_calls:
            wait = self.period_s - (self._clock() - self._calls[0])
            if wait > 0:
                self._sleep(wait)
            self._evict()

        self._calls.append(self._clock())

    def _evict(self) -> None:
        cutoff = self._clock() - self.period_s
        self._calls = [t for t in self._calls if t > cutoff]


class ComplexityBudget:
    """Token-bucket budget over an API's *complexity-point* allowance.

    Linear (design §Rate-limit budgets) limits API-key traffic to 3,000,000
    complexity points/hour via a **leaky bucket** — tokens refill continuously
    at `max_points / period_s`. This models that:

      - `reserve(cost)` draws `cost` tokens, sleeping until enough have refilled
        if the bucket is short.
      - `observe(remaining)` snaps the local bucket DOWN to the server's
        authoritative `X-RateLimit-Complexity-Remaining` header, so estimation
        error can never let us drift above the real remaining budget.

    Why points and not a request count: the binding constraint flips by
    operation — a cheap single-ticket fetch is request-bound, but a batched
    `sync_recent` page (~7,100 pts at `first: 50`) is complexity-bound. A flat
    "N requests/hr" cap models the wrong dimension for the batched path. See
    `design/ticket-rag.md` §Rate-limit budgets for the full derivation.

    `clock` / `sleep` are injected so tests drive virtual time with no real
    waiting and assert exactly when throttling occurs (mirrors `RateLimiter`).
    """

    def __init__(
        self,
        max_points: float,
        period_s: float = 3600.0,
        *,
        min_interval_s: float = 0.0,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if max_points <= 0:
            raise ValueError("max_points must be > 0")
        import time as _time

        self.max_points = float(max_points)
        self.period_s = float(period_s)
        self.min_interval_s = min_interval_s
        self._rate = self.max_points / self.period_s  # tokens per second
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep
        self._tokens = self.max_points  # bucket starts full
        self._last_refill = self._clock()
        self._last_request: float | None = None

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.max_points, self._tokens + elapsed * self._rate)
            self._last_refill = now

    def reserve(self, cost: float) -> None:
        """Block until `cost` points are available, then consume them.

        Raises ValueError if a single `cost` exceeds the whole budget ceiling —
        no amount of waiting could ever satisfy it (the caller must shrink the
        query; for Linear that's the 10,000-pt single-query cap, enforced
        separately and earlier by the client).
        """
        if cost > self.max_points:
            raise ValueError(
                f"single reservation of {cost:.0f} points exceeds the "
                f"{self.max_points:.0f}-point budget ceiling — it can never run"
            )
        # Politeness spacing (the slow-walk default).
        if self.min_interval_s and self._last_request is not None:
            gap = self.min_interval_s - (self._clock() - self._last_request)
            if gap > 0:
                self._sleep(gap)
        self._refill()
        if self._tokens < cost:
            self._sleep((cost - self._tokens) / self._rate)
            self._refill()
        self._tokens -= cost
        self._last_request = self._clock()

    def observe(self, remaining: float) -> None:
        """Reconcile with the server's authoritative remaining-points header.

        Snaps the local bucket DOWN to `remaining` when the server reports less
        than we modelled (we under-counted the query) — never UP, so if we
        over-counted we stay conservative (under-use budget) rather than risk
        exceeding the real limit.
        """
        self._refill()
        self._tokens = min(self._tokens, float(remaining))
