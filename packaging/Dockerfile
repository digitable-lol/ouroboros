# Ouroboros-Logger — full multi-language MCP server image.
#
# Bundles every toolchain so all five backends work out of the box AND
# `execute` can compile/run the instrumented code:
#   Python (stdlib) · JS/TS (node + @babel) · C/C++ (clang/libclang + gcc/g++)
#   · Elixir (erlang/OTP + elixir + mix) · git for the draft sandbox.
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

RUN apt-get update && apt-get install -y --no-install-recommends \
        clang gcc g++ \
        nodejs npm \
        erlang-base erlang-crypto elixir \
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

# Fail the build if the engine can't enumerate its backends.
RUN ouroboros languages

# Default to the MCP server; override the CMD to run any CLI subcommand
# (e.g. `docker run --rm ouroboros-logger languages`).
ENTRYPOINT ["ouroboros"]
CMD ["mcp"]
