"""Ouroboros-Logger: guaranteed function-level logging instrumentation.

The package is organised in three layers:

* ``ouroboros.languages`` — pluggable per-language transformers that inject the
  logging wrappers using a *locate-then-splice* strategy (native AST is used
  only to locate node ranges; the original source text is never reprinted, so
  comments and formatting survive untouched).
* ``ouroboros.sandbox`` — the draft/clean (``черновик``/``чистовик``) workspace:
  ``create`` / CRUD-with-wrap / ``execute`` / ``finish``.
* ``ouroboros.mcp`` — an MCP stdio server exposing the engine to AI agents.
"""

__version__ = "0.1.0"
