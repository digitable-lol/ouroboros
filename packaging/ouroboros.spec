# PyInstaller spec — single-file `ouroboros` binary.
#
# Python and C backends work fully standalone (libclang's native lib is bundled).
# JS/C++/Elixir backends are included too but need their toolchain in PATH at
# runtime (node, g++, elixir) — and `execute` needs the relevant compiler/runtime.
#
# Build:  uv run pyinstaller packaging/ouroboros.spec --noconfirm
# Result: dist/ouroboros   (run `./dist/ouroboros mcp` for the MCP server)

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(SPECPATH)  # project root (parent of packaging/)

datas, binaries, hiddenimports = [], [], []

# libclang native lib + bindings (so C/C++ parsing works without a system clang)
for pkg in ("clang", "libclang"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Runtime assets read at run time via __file__ (must be physically present).
datas += [
    (os.path.join(ROOT, "ouroboros/runtime.py"), "ouroboros"),
    (os.path.join(ROOT, "ouroboros/languages/_c"), "ouroboros/languages/_c"),
    # The C/C++ range emitter ships as SOURCE and is compiled on first use into
    # the user's cache, so one frozen binary works with whatever libclang the
    # machine has. Leaving it out makes every C and C++ wrap fail at run time.
    (os.path.join(ROOT, "ouroboros/languages/_clang"), "ouroboros/languages/_clang"),
    (os.path.join(ROOT, "ouroboros/languages/_cpp"), "ouroboros/languages/_cpp"),
    (os.path.join(ROOT, "ouroboros/languages/_js"), "ouroboros/languages/_js"),
    (os.path.join(ROOT, "ouroboros/languages/_elixir"), "ouroboros/languages/_elixir"),
]

hiddenimports += [
    "ouroboros.languages.python_lang",
    "ouroboros.languages.javascript",
    "ouroboros.languages.clangbridge",
    "ouroboros.languages.c_lang",
    "ouroboros.languages.cpp_lang",
    "ouroboros.languages.elixir_lang",
    "ouroboros.mcp.server",
    "mcp.server.fastmcp",
]

a = Analysis(
    [os.path.join(ROOT, "packaging/entry.py")],
    pathex=[ROOT],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["mcp.cli"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ouroboros",
    console=True,
    strip=False,
    upx=False,
)
