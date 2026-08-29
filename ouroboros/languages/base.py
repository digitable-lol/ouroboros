"""Core *locate-then-splice* infrastructure shared by every language backend.

The cardinal rule of Ouroboros transformers: **never reprint a whole file**.
A backend parses the source with a native parser purely to discover the byte
ranges of interesting nodes (function headers, ``return`` statements), then
emits a list of :class:`Edit` operations against the *original* source text.
Applying those edits preserves every comment, blank line and formatting choice
the author (human or agent) made — which matters because the sandbox commits
each saved file to git and later syncs the draft to the clean tree.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class CorruptedSourceError(Exception):
    """Raised when a backend cannot parse the source it was handed.

    This is the prototype's "filesystem refuses to persist low-quality code"
    gate: the sandbox turns a parse failure into a rejected write instead of
    saving a broken file. It deliberately requires no LSP/YouCompleteMe — a
    failed native parse *is* the corruption signal.
    """

    def __init__(self, language: str, detail: str, *, filename: str | None = None):
        self.language = language
        self.detail = detail
        self.filename = filename
        where = f" in {filename}" if filename else ""
        super().__init__(f"[{language}] corrupted source{where}: {detail}")


@dataclass(frozen=True, order=True)
class Edit[T: (str, bytes)]:
    """A single replacement over ``[start, end)`` of the original source.

    ``start == end`` is a pure insertion. Offsets index into the original
    (pre-edit) buffer: character indices for the ``str`` backends (Python, JS,
    Elixir), byte offsets for the ``bytes`` backends (C, C++) — libclang reports
    byte offsets, so those transformers splice the raw bytes. ``T`` ties
    ``replacement`` to whichever the backend uses; :func:`apply_edits` keeps the
    source and its edits in the same string kind.
    """

    start: int
    end: int
    replacement: T

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid edit range: [{self.start}, {self.end})")


@dataclass(frozen=True)
class WrapResult:
    """Outcome of wrapping one source buffer — the metrics MCP reports back."""

    code: str
    language: str
    functions_wrapped: int
    # Non-fatal advisories about the wrap — e.g. the parse used degraded flags
    # because the file isn't in the tree's compile_commands.json, so functions in
    # inactive #ifdef branches may have been missed. Empty when nothing to flag.
    warnings: tuple[str, ...] = ()


def apply_edits[T: (str, bytes)](source: T, edits: list[Edit[T]]) -> T:
    """Apply non-overlapping ``edits`` to ``source`` and return the new text.

    Edits are applied right-to-left so earlier offsets stay valid. Overlapping
    edits are a programming error in a backend and raise ``ValueError`` rather
    than silently corrupting output. Insertions that share an offset are kept
    in their original list order.
    """

    ordered = sorted(enumerate(edits), key=lambda ie: (ie[1].start, ie[1].end, ie[0]))
    prev_end: int | None = None
    for _, e in ordered:
        if prev_end is not None and e.start < prev_end:
            raise ValueError(
                f"overlapping edits: range starting at {e.start} overlaps "
                f"previous edit ending at {prev_end}"
            )
        # An insertion (start == end) at exactly prev_end does not overlap.
        prev_end = max(prev_end if prev_end is not None else e.end, e.end)

    out = source
    for _, e in sorted(enumerate(edits), key=lambda ie: (ie[1].start, ie[1].end, ie[0]),
                       reverse=True):
        out = out[: e.start] + e.replacement + out[e.end :]
    return out


def line_start_offsets(source: str) -> list[int]:
    """Return a list ``offs`` where ``offs[n]`` is the char offset at which the
    1-based line ``n`` begins. ``offs[0]`` is unused (lines are 1-based to match
    AST ``lineno``)."""

    offsets = [0, 0]  # index 0 unused; line 1 starts at 0
    for i, ch in enumerate(source):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def leading_whitespace(source: str, line_offset: int) -> str:
    """Leading whitespace (indentation) of the physical line at ``line_offset``."""

    i = line_offset
    n = len(source)
    while i < n and source[i] in " \t":
        i += 1
    return source[line_offset:i]


class Transformer(ABC):
    """Base class for a language backend.

    Subclasses declare which file extensions they own and implement
    :meth:`wrap_source`. The interface is uniform across languages even though
    the mechanics differ (decorator injection for Python, ``try/finally`` body
    rewriting for C#, RAII ``ScopeGuard`` for C++).
    """

    language: str
    extensions: tuple[str, ...]

    @abstractmethod
    def wrap_source(self, source: str, *, filename: str | None = None,
                    only: set[str] | None = None,
                    minimal: bool = False) -> WrapResult:
        """Instrument ``source`` and return the wrapped code plus metrics.

        Must raise :class:`CorruptedSourceError` if ``source`` does not parse.
        Must be idempotent: wrapping already-wrapped code is a no-op.

        ``only`` — when given, a set of function names; ONLY those functions are
        instrumented and every other definition is left untouched. This is the
        selective, opt-in mode required for hot/kernel files where blanket
        wrapping would flood the sink or take a per-frame struct on a tiny
        kernel stack inside a critical section. ``None`` wraps everything.

        ``minimal`` — C only: emit a stackless, depth-only probe (a 1-byte cleanup
        marker + a depth-stamped IN-record) instead of the full per-frame struct.
        For HOT/RECURSIVE/deeply-locked kernel functions where the full struct
        blows the kernel stack or widens the fault surface. Backends that do not
        support it raise ``NotImplementedError`` when it is set.
        """

    def runtime_asset(self) -> tuple[str, str] | None:
        """Return ``(filename, source)`` of the runtime helper this language's
        instrumented code imports, or ``None`` if it needs no helper file.

        The sandbox drops this file into the draft so wrapped code can run. All
        helpers must emit the same ``debug.info`` schema (see SPEC.md)."""

        return None
