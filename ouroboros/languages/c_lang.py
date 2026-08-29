"""C backend — type-directed instrumentation via `__attribute__((cleanup))`.

C has no runtime value representation and no RAII/exceptions, so this backend is
qualitatively different from the Python/JS ones:

* **Type-directed formatting.** Each parameter's and the return value's static
  type decides a printf specifier (``int``→``%d``, ``long``→``%ld``,
  pointer→``%p``, ``const char*``→guarded ``%s``…). Non-scalar/unprintable types
  degrade to ``<...>`` / ``(no value)``. The types are read by the range emitter
  (``_clang/emitter.c``), which reports the specifier per parameter; this module
  only writes the text.
* **Scope exit via `__attribute__((cleanup))`.** A GCC/Clang extension (NetBSD
  builds with both, and uses the idiom). A cleanup-attributed context emits the
  ``out`` JSONL line on EVERY exit path — return, ``goto out``, fall-through —
  which is the C equivalent of try/finally and satisfies the SPEC.

All offsets from the emitter are BYTE offsets, so this backend splices on
``bytes`` (never ``str``) to stay correct on non-ASCII source. Scope: userland C
(the runtime uses stdio); kernel/freestanding needs a different sink.

The parse itself happens in another process — see ``clangbridge``. Nothing below
knows what a cursor or a type is.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .base import Edit
from .clangbridge import ClangFunction, ClangTransformer, clang_resource_dir_args

_C_DIR = Path(__file__).parent / "_c"

_INCLUDE = '#include "ouroboros_runtime.h"'


@lru_cache(maxsize=1)
def _clang_args() -> list[str]:
    """Parse flags for **self-contained** code: clang builtin headers + the host
    /usr/include, so a well-formed snippet parses with zero diagnostics. Files
    that belong to a real source tree instead get that tree's flags + the tree
    compiler's predefs via an `.ouroboros.json` config — see `treeflags.py`."""
    return ["-std=gnu11", "-ferror-limit=0", *clang_resource_dir_args(),
            "-isystem", "/usr/include"]


class CTransformer(ClangTransformer):
    language = "c"
    extensions = (".c", ".h")

    runtime_dir = _C_DIR
    runtime_name = "ouroboros_runtime.h"
    include_line = _INCLUDE
    default_filename = "input.c"
    supports_minimal = True

    def default_args(self) -> list[str]:
        return _clang_args()

    def include_anchor(self, raw: bytes, first: ClangFunction) -> int:
        """Start of the line the FIRST instrumented function begins on.

        Robustly AFTER every #include/#define/#if block (the parser LOCATED the
        function, so this survives multi-line macros, ``#if 0`` dead code and
        RCSID boilerplate that defeat text scanning) and BEFORE any probe use.
        """
        return raw.rfind(b"\n", 0, first.extent_start) + 1

    def instrument(self, fn: ClangFunction, *, minimal: bool = False) -> list[Edit[bytes]]:
        name = fn.name
        open_off = fn.body_start + 1

        # ---- minimal probe: stackless, depth-only (hot/recursive/locked kernel) ----
        # No per-frame struct, no args, no return rewriting — just a 1-byte cleanup
        # marker initialised to _ouro_min_enter's return (1 = recorded, 0 = skipped by
        # the re-entrancy guard), so _ouro_min_exit balances the depth. The `__ouro`
        # token keeps idempotency; the cleanup attribute marks it used.
        if minimal:
            entry = (
                "\n\tchar __ouro __attribute__((cleanup(_ouro_min_exit)))"
                f' = _ouro_min_enter("{name}");\n'
            ).encode()
            return [Edit(open_off, open_off, entry)]

        # ---- argument format (type-directed) ----
        fmt_parts, arg_exprs = [], []
        # Values only, comma-separated. SPEC.md splits the two fields: `a` carries
        # positional values, `k` carries name=value pairs (and C, having no named
        # arguments, emits `k` empty). Writing "a=2, b=3" into `a` put names in
        # the field that must not hold them, and made the C trace uncomparable
        # with the Python and JS ones for the same call.
        for p in fn.params:
            pname = p.name or "_"
            if p.spec is None:
                fmt_parts.append("<...>")
            elif p.is_string:
                fmt_parts.append("%s")
                arg_exprs.append(f'({pname} ? {pname} : "(null)")')
            else:
                fmt_parts.append(p.spec)
                arg_exprs.append(pname)
        fmt = ", ".join(fmt_parts)
        tail = ("" if not arg_exprs else ", " + ", ".join(arg_exprs))

        # ---- return-value capture plan ----
        capture = fn.result.spec is not None and fn.result.temp_type is not None

        # ---- entry injection (just after the body's `{`) ----
        lines = ["", "\tstruct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)));"]
        if capture:
            lines.append(f"\t{fn.result.temp_type} __ouro_result;")
        lines.append(f'\t_ouro_enter(&__ouro, "{name}", "{fmt}"{tail});')
        entry = ("\n".join(lines) + "\n").encode("utf-8")
        edits: list[Edit[bytes]] = [Edit(open_off, open_off, entry)]

        # ---- return-value capture at each return-with-value ----
        if capture:
            res_arg = ('(__ouro_result ? __ouro_result : "(null)")'
                       if fn.result.is_string else "__ouro_result")
            for ret in fn.returns:
                if ret.arg_start is None or ret.arg_end is None:
                    continue  # bare `return;` — cleanup handles it
                post = (f"), _ouro_set_result(&__ouro, \"{fn.result.spec}\", "
                        f"{res_arg}), __ouro_result)").encode()
                edits.append(Edit(ret.arg_start, ret.arg_start, b" (__ouro_result = ("))
                edits.append(Edit(ret.arg_end, ret.arg_end, post))
        return edits
