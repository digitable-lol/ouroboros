"""Ouroboros-Logger: guaranteed function-level logging instrumentation.

The package is organised in three layers:

* ``ouroboros.languages`` — pluggable per-language transformers that inject the
  logging wrappers using a *locate-then-splice* strategy (native AST is used
  only to locate node ranges; the original source text is never reprinted, so
  comments and formatting survive untouched).
* ``ouroboros.sandbox`` — the draft/clean (``draft/``/``clean/``) workspace:
  ``create`` / CRUD-with-wrap / ``execute`` / ``finish``.
* ``ouroboros.mcp`` — an MCP stdio server exposing the engine to AI agents.
"""

from importlib.metadata import PackageNotFoundError, version as _installed_version

#: The package version. Read from the installation's metadata, NOT typed in here
#: as a literal.
#:
#: Typed in as a literal, it once stayed at ``0.1.0`` across four releases: the
#: string was never read, so nobody noticed it was lying. The number is entered
#: in one place — ``[project] version`` in ``pyproject.toml`` — and installing
#: puts it into the distribution's metadata, which is where this reads it from.
#: There is no longer anywhere for the two to drift apart.
#:
#: A tree that is not installed (imported straight from a clone) has no such
#: metadata. Then the version is unknown, and that is said plainly rather than
#: papered over with a plausible number. ``scripts/check_release_published.py``
#: watches for a literal coming back here — the "version in the package" rule.
try:
    __version__ = _installed_version("ouroboros-logger")
except PackageNotFoundError:  # pragma: no cover — a tree that is not installed
    __version__ = "0+unknown"
