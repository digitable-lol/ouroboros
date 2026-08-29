"""Pluggable per-language transformers (locate-then-splice instrumentation)."""

from .base import (
    CorruptedSourceError,
    Edit,
    Transformer,
    WrapResult,
    apply_edits,
)
from .registry import (
    supported_extensions,
    supported_languages,
    transformer_for_extension,
    transformer_for_language,
    transformer_for_path,
)
from .treeflags import TreeConfigError

__all__ = [
    "CorruptedSourceError",
    "TreeConfigError",
    "Edit",
    "Transformer",
    "WrapResult",
    "apply_edits",
    "supported_extensions",
    "supported_languages",
    "transformer_for_extension",
    "transformer_for_language",
    "transformer_for_path",
]
