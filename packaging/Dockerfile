# Ouroboros-Logger — full multi-language MCP server image.
#
# Bundles every toolchain so all six backends work out of the box AND
# `execute` can compile/run the instrumented code:
#   Python (stdlib) · JS/TS (node + @babel) · C/C++ (clang/libclang + gcc/g++)
#   · Elixir (erlang/OTP + elixir + mix) · Go (its own parser ships with it)
#   · git for the draft sandbox
#   · clangd + clang-tidy for the six C/C++ navigation tools.
#
# Build:  docker build -t ouroboros-logger -f packaging/Dockerfile .
# Run  :  docker run --rm -i ouroboros-logger          # MCP server over stdio
#
# An MCP client launches it as:  docker run --rm -i ouroboros-logger
# For hosted/multi-tenant use, run one container PER session and add resource
# limits + network isolation — `execute` runs agent-provided code.

# Python is the primary runtime (>=3.12). The language toolchains are added on
# top via apt: erlang+elixir, node+npm, clang/gcc/g++, git. To instrument Elixir
# for a specific target's BEAM version, install that version instead of Debian's.
FROM python:3.12-slim-bookworm

# clangd and clang-tidy back six of the seventeen MCP tools (symbol_search,
# document_symbols, references, call_hierarchy, describe_symbol, lint_file).
# They were missing here, so those six were declared by the server and then
# failed for want of a binary — a third of the tool surface, advertised and
# broken, in every image built from this file.
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang clangd clang-tidy gcc g++ \
        nodejs npm \
        erlang-base erlang-crypto elixir \
        golang-go \
        git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Editable install keeps the package rooted at /app, so the JS emitter's
# node_modules (installed next to it) is found at runtime; libclang ships bundled
# as a pip dependency.
RUN pip install --no-cache-dir -e .

# JS emitter dependencies (@babel/parser).
RUN cd /app/ouroboros/languages/_js && npm install --omit=dev --no-audit --no-fund

# Note on Elixir compile-order: `use Ouroboros.Trace` expands at compile time, so
# the shipped runtime (ouroboros/languages/_elixir/ouroboros_trace.ex) must be
# compiled before any instrumented module. The sandbox drops it into each draft
# project; compile it first there (the per-project step, not a global one).

# Build the C/C++ range emitter now rather than on the first wrap, and fail the
# image build if it cannot be built. It is a small C program over libclang
# (ouroboros/languages/_clang/emitter.c); the gcc installed above builds it.
RUN python -c "from ouroboros.languages.clangbridge import emitter_path; print(emitter_path())"

# Same for the Go range emitter (ouroboros/languages/_go/emitter.go). It needs
# nothing but the Go toolchain installed above — go/parser ships with the
# language — and building it here means the image fails loudly rather than the
# first wrap of a .go file.
RUN python -c "from ouroboros.languages.go_lang import emitter_path; print(emitter_path())"

# Fail the build if the engine can't enumerate its backends.
RUN ouroboros languages

# Fail the build if the binaries behind the six C/C++ tools are absent. Without
# this the image builds happily and the tools break only when an agent calls one.
RUN clangd --version && clang-tidy --version

# Default to the MCP server; override the CMD to run any CLI subcommand
# (e.g. `docker run --rm ouroboros-logger languages`).
ENTRYPOINT ["ouroboros"]
CMD ["mcp"]
