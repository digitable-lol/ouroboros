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

from importlib.metadata import PackageNotFoundError, version as _installed_version

#: Версия пакета. Берётся из метаданных установки, а НЕ вписывается сюда числом.
#:
#: Вписанная числом, она однажды осталась ``0.1.0`` и пережила так четыре выпуска:
#: строку никто не читал, поэтому никто и не заметил, что она врёт. Число заводится
#: в одном месте — ``[project] version`` в ``pyproject.toml``; при установке оно
#: попадает в метаданные дистрибутива, откуда и читается. Разойтись им теперь негде.
#:
#: Дерево без установки (импорт прямо из склонированного хранилища) метаданных не
#: имеет. Тогда версия неизвестна, и это сказано прямо, а не подменено правдоподобным
#: числом. Чтобы литерал не вернулся сюда снова, за этим следит
#: ``scripts/check_release_published.py``, правило «версия в пакете».
try:
    __version__ = _installed_version("ouroboros-logger")
except PackageNotFoundError:  # pragma: no cover — дерево без установки
    __version__ = "0+unknown"
