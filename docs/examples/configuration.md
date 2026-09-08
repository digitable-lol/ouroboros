---
title: Configuration
---

**English** · [Русский](configuration.ru.md)

# Configuration

There is almost nothing to configure in Ouroboros: two places — hooking up the
MCP server, and two environment variables. Everything below was checked by a
run.

## Hooking up the MCP server

Needed only if an AI agent is going to use the tool. For working by hand the
`ouroboros` command is enough.

After an install (uv, Homebrew, asdf) the `ouroboros-mcp` command sits on
`PATH`:

```json
{ "mcpServers": { "ouroboros": { "type": "stdio", "command": "ouroboros-mcp" } } }
```

If you work from a clone of the repository and would rather install nothing:

```json
{ "mcpServers": { "ouroboros": {
    "type": "stdio", "command": "uv",
    "args": ["run", "--directory", "<path to the repository>", "ouroboros-mcp"] } } }
```

| field | what it means |
|---|---|
| `mcpServers` | the list of outside tools the agent may call |
| `ouroboros` | the name the server will go by for the agent |
| `"type": "stdio"` | the conversation runs over the started process's ordinary input and output |
| `"command"` | what to start: `ouroboros-mcp` itself, or `uv` |
| `"args"` | needed only for `uv`: where the repository is and what to run in it |

Where this block goes — [Install](../install.md#where-this-block-goes).

Exactly this configuration sits in the repository in the file
[`.mcp.json`](https://github.com/digitable-lol/ouroboros/blob/main/.mcp.json) —
Claude Code picks it up from the project root on its own.

### Check it without an agent

The server talks plain JSON-RPC over its input and output, so you can question
it by hand:

```sh
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
 | ouroboros-mcp
```

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05", … ,"serverInfo":{"name":"ouroboros-logger","version":"1.27.2"}}}
```

> `1.27.2` is the version of the MCP library, not of Ouroboros. The library
> stamps it there itself; the tool's version lives in `pyproject.toml`.

The list of tools the server announces in answer to `tools/list` — seventeen
names:

```
wrap_code_snippet   wrap_file        wrap_functions
read_trace          trace_stats
create_project      write_file       read_file       list_files   execute   finish
lint_file           symbol_search    document_symbols
references          call_hierarchy   describe_symbol
```

What each one does — [Working with AI](../with-ai.md).

## Environment variables

There are two, both read in the source, and there are no others.

| variable | what it does |
|---|---|
| `OUROBOROS_DEBUG_INFO` | path to the trace file. Not set — records go to `./debug.info` in the process's working directory. `ouroboros execute` sets it itself, pointing at `<черновик>/debug.info` |
| `OUROBOROS_MCP_TRANSPORT` | how the MCP server talks: `stdio` (the default), `sse` or `streamable-http`. Anything else — the server refuses to start and names the values it takes |

Where they are read:
[`ouroboros/runtime.py:62`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L62)
and
[`ouroboros/mcp/server.py:908`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/mcp/server.py#L908).

An example: put the records into a file of their own instead of next to the
program.

```sh
OUROBOROS_DEBUG_INFO=/path/probe.jsonl python3 stats.py
```

## What has to be installed

| needed | when |
|---|---|
| Python 3.12 or newer | always |
| `gcc` or `clang` | to build instrumented C |
| `g++` or `clang++` | to instrument and build C++ |
| Node | to instrument and run JavaScript and TypeScript |
| `elixir` | to build and run Elixir |
| `go` | to instrument, build and run Go |
| JDK (`javac`, `java`) | to instrument and build Java |
| .NET SDK (`dotnet`) | to instrument and build C# |
| `clang-tidy`, `clangd` | only for `lint`, `symbols`, `refs`, `callers`, `describe` |

`libclang` and `@babel/parser` are packed inside the package: there is no need
to bring them separately. No separate service, database or external key is
required.

## What can be tuned in the instrumentation itself

Almost nothing — and that is on purpose. Tuning the instrumentation means
choosing **what** to instrument, and that is done with command arguments, not
with a settings file:

| needed | how |
|---|---|
| instrument part of a file | `wrap-functions <file> <name>…` |
| see the result without touching the file | `wrap-file <file> --stdout` |
| lighter records for hot functions (C only) | `--minimal` |

The tool has no settings file.
