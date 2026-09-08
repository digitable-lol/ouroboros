---
title: Install
---

**English** · [Русский](install.ru.md)

# Install

Four ways to install the tool, and one optional step after — connecting it to an
AI agent as an MCP server.

## What you need on the machine

| needed | when |
|---|---|
| Python 3.12 or newer | always |
| `gcc` or `clang` | to instrument and build C |
| `g++` or `clang++` | to instrument and build C++ |
| Node | to instrument and run JavaScript and TypeScript |
| `elixir` | to build and run Elixir |
| `go` | to instrument, build and run Go |
| JDK (`javac`, `java`) | to instrument and build Java |
| .NET SDK (`dotnet`) | to instrument and build C# |
| `clang-tidy`, `clangd` | only for the `lint`, `symbols`, `refs`, `callers`, `describe` commands |

`libclang` (parsing C and C++) comes with the package — it is a dependency, you
do not fetch it separately. Parsing JavaScript and TypeScript is packed inside
too: `@babel/parser` ships in the package itself, no `npm install` is needed,
only `node`. The Go parser needs nothing from outside: `go/parser` is part of
the language distribution, which is why `go` is needed already at instrumentation
time, not only at build time. The Java and C# parsers need no fetching either,
and for the same reason: they already sit inside the build tools themselves. For
Java that is the JDK compiler (`javax.tools` together with `com.sun.source`),
for C# it is Roslyn inside the .NET SDK package; the path to it is found from
`dotnet --list-sdks`, not written down in the tree.

Parsing C and C++ lives in a separate small C program. It is built once per
machine on the first instrumenting run and put into the user cache, which is why
a C and C++ compiler is needed already at instrumentation time. llvm headers are
not needed for it: the program declares the slice of libclang it uses by itself,
and a separate check verifies that those declarations are right. If your
distribution carries a prebuilt program, point `OUROBOROS_CLANG_EMITTER` at it,
and then no compiler is needed at instrumentation time.

The Java and C# parsers work the same way: they are programs in Java and in C#,
built once per machine and put into the cache (0.73 s and 15 KB for Java, 1.87 s
and 32 MB for C# — [Measurements](measurements.md#parser-cost-in-java-and-c)),
which is why the JDK and the .NET SDK are needed already at instrumentation time.
Prebuilt ones are given by `OUROBOROS_JAVA_EMITTER` and
`OUROBOROS_CSHARP_EMITTER`.

No separate service, database or external key is required.

## If the install stops at “Permission denied (publickey)”

This is the most common trouble, and the repositories are not to blame: they are
public and read over `https` without any key at all. The blame is on the git
setup on your machine.

All four ways below fetch files from GitHub through `git` at some point. If your
git setup has an `insteadOf` rule that rewrites `https://github.com/…` addresses
into `git@github.com:…`, then that step silently goes over ssh — and fails if
you have no key or it is not registered on GitHub. People who write into those
same repositories often set the rule up for themselves.

The tell is right there in the failure: you asked for an `https` address, and
the complaint came back about `git@github.com` and a key. To see whether the
rule is there:

```sh
git config --get-regexp 'url\..*\.insteadof'
```

```
url.git@github.com:digitable-lol/.insteadof https://github.com/digitable-lol/
```

Where each way breaks:

| way | at which step | what you see |
|---|---|---|
| uv | `uv tool install git+https://…` | `git fetch … Permission denied (publickey)` |
| Homebrew | attaching the formula repository: `Cloning into '…/Taps/digitable-lol/homebrew-tap'` | `Permission denied (publickey)` |
| asdf | `asdf plugin add` | `unable to clone plugin: and the repository exists.` — `asdf` shows only the last line of somebody else's complaint, so the message looks like nonsense |
| from source | `git clone` | `Permission denied (publickey)` |

**The cure is replacing `insteadOf` with `pushInsteadOf`.** Then only pushing is
substituted, and reading stays on `https`:

```sh
git config --global --unset url."git@github.com:digitable-lol/".insteadOf
git config --global url."git@github.com:digitable-lol/".pushInsteadOf https://github.com/digitable-lol/
```

Nothing convenient is lost: `git push` still goes to `git@github.com` with your
key, only reading changes.

What to know in advance:

- **Slipping `GIT_CONFIG_GLOBAL=/dev/null` in just for `brew` will not work.**
  `brew` scrubs the environment before it calls git and keeps only the names it
  knows; `GIT_CONFIG_GLOBAL` is not on that list (see `Homebrew/bin/brew`,
  variable `ENV_VAR_NAMES`). For `asdf` and `uv` such a substitution does work,
  but the rule is still easier to fix once.
- **The source archive itself is untouched by the rule.** Both the Homebrew
  formula and the asdf plugin fetch the release with an ordinary `https`
  download (`…/archive/refs/tags/v0.5.0.tar.gz`), not through git. Exactly one
  step breaks — the one where a repository is **cloned**.
- If you cannot edit the git setup, what is left is cloning the repository you
  need by hand from the direct `git@…` address, or handing over a local copy.
  There is no way around this from inside the formula or the plugin: the cloning
  is done not by them but by `brew` itself (`asdf`, `uv`).

## Way 1. uv

The shortest one, and it needs no release:

```sh
uv tool install git+https://github.com/digitable-lol/ouroboros
```

```
Installed 2 executables: ouroboros, ouroboros-mcp
```

Check:

```sh
ouroboros languages
```

```json
{"languages": ["python", "javascript", "c", "cpp", "elixir"]}
```

Upgrade and uninstall:

```sh
uv tool upgrade ouroboros-logger
uv tool uninstall ouroboros-logger
```

The package is named `ouroboros-logger`, the commands are `ouroboros` and
`ouroboros-mcp`.

The `git+` prefix means `uv` takes the sources through git. If you have an
`insteadOf` rule, this way stops at “Permission denied (publickey)” — see
[the section above](#if-the-install-stops-at-permission-denied-publickey).

## Way 2. Homebrew

```sh
brew install digitable-lol/tap/ouroboros
```

The first part of the name — `digitable-lol/tap` — is a separate formula
repository (Homebrew calls it a tap; the full repository name is
`digitable-lol/homebrew-tap`). `brew` attaches it itself, a separate `brew tap`
command is not needed.

The formula source lives here, in
[`packaging/homebrew/ouroboros.rb`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/homebrew/ouroboros.rb),
and the published copy is in the tap itself:
[`digitable-lol/homebrew-tap`](https://github.com/digitable-lol/homebrew-tap),
file `Formula/ouroboros.rb`. Edits go into the source, the tap gets a copy at
release time.

The formula installs the package into its own Python environment and exposes
both commands. Homebrew brings Python 3.12 along — you do not install it
separately.

> **About the “is not trusted” line.** Starting with Homebrew 6.0, taps that are
> not your own require consent. The install output flashes
>
> ```
> Warning: Skipping digitable-lol/tap because it is not trusted. Run `brew trust digitable-lol/tap` to trust it.
> ==> Trusted formula digitable-lol/tap/ouroboros
> ```
>
> No need to stop: you named the formula in full, and `brew` counts that as
> consent to it — the install carries on and the consent is remembered
> (`~/.homebrew/trust.json`). Short names work after that: `brew info
> ouroboros`, `brew test ouroboros`, `brew upgrade ouroboros`. To stop the
> warning from flashing and extend the consent to the whole repository, say
> once:
>
> ```sh
> brew trust digitable-lol/tap
> ```

Verified by a run on 30 August 2026. The run started from nothing: the previous
install removed (`brew uninstall ouroboros` took Python 3.12 with it), the
formula repository detached (`brew untap`), after which one line, `brew install
digitable-lol/tap/ouroboros`, attached the repository itself, installed eighteen
dependencies together with Python 3.12 and the package itself in a little over a
minute. Then: `brew test ouroboros` passed; the installed command printed all
eight languages; the same command instrumented a real four-function Python file
(names in Cyrillic letters, one function raises) — the output before and after
instrumenting matched byte for byte, six calls in the records, zero malformed
lines, zero unfinished; `ouroboros-mcp` answered `initialize` over JSON-RPC.

> **If `brew` refuses over access rights** and `git@github.com: Permission denied
> (publickey)` flashes in the complaint — that is the `insteadOf` rule in your
> git setup, not a ban on the formula repository. What to do —
> [the section above](#if-the-install-stops-at-permission-denied-publickey).

Upgrading and uninstalling — as with any formula:

```sh
brew upgrade ouroboros
brew uninstall ouroboros
```

## Way 3. asdf

`asdf` keeps several versions of one tool side by side and switches between them
by the `.tool-versions` file in the project.

```sh
asdf plugin add ouroboros https://github.com/digitable-lol/ouroboros.git
asdf install ouroboros latest
asdf set ouroboros latest
```

Versions are taken straight from the repository tags:

```sh
asdf list all ouroboros
```

```
0.2.0
0.2.1
0.3.0
0.3.1
0.4.0
0.5.0
```

On asdf older than 0.16 the last line is written as `asdf local ouroboros latest`.

> **About the short name.** `asdf plugin add ouroboros` without an address looks
> the plugin up in the shared asdf plugin list; getting in there is a separate
> step that has not been taken yet. Until then the repository address is given
> explicitly, as above.

The plugin exposes exactly two commands — `ouroboros` and `ouroboros-mcp`. That
matters: along with the package, the commands of its dependencies arrive into
its environment — `httpx`, `jsonschema`, `mcp`, `uvicorn`, `dotenv` and others.
Expose everything in a row and `asdf` makes a shim for every name, and those
shims will shadow the real programs with the same names on the machine.

Verified by a run on 30 August 2026 on asdf 0.20.0, release
<!--state:version-->0.5.0<!--/state-->, from an empty slate: `plugin add` with
the address above, `list all ouroboros` (prints six versions, from `0.2.0` to
`0.5.0`), `install ouroboros 0.5.0` (installs from source in twelve seconds),
`set ouroboros 0.5.0`. After that `asdf` had made exactly two shims —
`ouroboros` and `ouroboros-mcp`, not one extra — and the installed command
instrumented a real four-function Python file: the output before and after
instrumenting matched byte for byte, six calls in the records, zero malformed
lines, zero unfinished. `ouroboros-mcp` answered `initialize` over JSON-RPC
through the shim.

> **If `asdf plugin add` answers with nonsense** such as
>
> ```
> unable to clone plugin: and the repository exists.
> ```
>
> — that is the `insteadOf` rule in your git setup. `asdf` shows only the last
> line of what git said, and what git said was “Permission denied (publickey) …
> Please make sure you have the correct access rights and the repository
> exists.” What to do —
> [the section above](#if-the-install-stops-at-permission-denied-publickey).
>
> The cloning is done by `asdf` itself, so there is no defending against it from
> inside the plugin. Its own calls to git the plugin does defend: `bin/list-all`
> sets `GIT_CONFIG_GLOBAL=/dev/null`, and `asdf list all ouroboros` prints
> versions even with the rule in force — that was checked separately.

How the plugin is built —
[`packaging/asdf/README.md`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/asdf/README.md).

## Way 4. From source

```sh
git clone https://github.com/digitable-lol/ouroboros
cd ouroboros
uv sync
```

The `insteadOf` rule breaks this way too — on the `git clone` itself; see
[the section above](#if-the-install-stops-at-permission-denied-publickey).

From there, either through `uv run`:

```sh
uv run ouroboros languages
uv run ouroboros-mcp        # MCP server over plain stdio
```

or check everything at once:

```sh
scripts/qa.sh
```

```
== ruff (lint, ouroboros + tests) ==
All checks passed!
== mypy (strict, ouroboros) ==
Success: no issues found in 24 source files
== pytest ==
........................................................................ [ 12%]
........................................................................ [ 24%]
   …
....                                                                     [100%]
580 passed in 237.23s (0:03:57)
== all gates passed ==
```

Eight of these checks need `clang-tidy` and `clangd`. If they are not on the
machine, the checks are skipped, and at the end you get `159 passed, 8 skipped`
— that is a green result too.

## A single-file program and an image

Beyond this, the package is built into **one self-contained file**
(PyInstaller, about 47 MB, no Python needed on the machine) and into **an image
with all the languages at once**. Both ways are written out in
[`packaging/README.md`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/README.md):

```sh
uv run pyinstaller packaging/ouroboros.spec --noconfirm
./dist/ouroboros languages
```

```sh
docker build -t ouroboros-logger -f packaging/Dockerfile .
docker run --rm -i ouroboros-logger
```

## Connecting the MCP server

You need this only if you want an AI agent to use the tool. For working by hand
the `ouroboros` command is enough.

After installing through uv, Homebrew or asdf the `ouroboros-mcp` command is on
`PATH`, and the setup is short:

```json
{ "mcpServers": { "ouroboros": { "type": "stdio", "command": "ouroboros-mcp" } } }
```

If you work from source and do not want to install anything:

```json
{ "mcpServers": { "ouroboros": {
    "type": "stdio", "command": "uv",
    "args": ["run", "--directory", "<path to the repository>", "ouroboros-mcp"] } } }
```

Exactly this setup lies in the repository in the file
[`.mcp.json`](https://github.com/digitable-lol/ouroboros/blob/main/.mcp.json) —
Claude Code picks it up from the project root by itself.

### Where this block goes

| client | where it usually lives |
|---|---|
| Claude Code | `.mcp.json` in the project root — travels with the repository and reaches the whole team |
| Cursor | `.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` for all projects at once |
| any other client | its own settings file for external tools; check its documentation |

The block is built the same way everywhere: server name, `type`, `command`, and
`args` if needed. If your client expects the setting without the outer
`mcpServers` — keep the inside.

### Checking that it connected

Ask the agent for the list of tools available to it. **Seventeen** names should
appear, starting with `wrap_file`, `read_trace` and `trace_stats`. The full list
and what each one does — [Working with AI](with-ai.md).

You can also check the server without an agent, by hand — it speaks plain
JSON-RPC over stdio:

```sh
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
 | ouroboros-mcp
```

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05", … ,"serverInfo":{"name":"ouroboros-logger","version":"1.29.1"}}}
```

> The number in `serverInfo` is the version of the MCP library, not the version
> of Ouroboros. The library fills it in itself, so you will have whichever one
> arrived at install time; in the run above it is `1.29.1`. The version of the
> tool itself lies in `pyproject.toml` and right now equals
> `<!--state:version-->0.5.0<!--/state-->`.

## Environment variables

| variable | what it does |
|---|---|
| `OUROBOROS_DEBUG_INFO` | path to the trace file. Not set — records go to `./debug.info` next to the working directory. `ouroboros execute` sets it itself |
| `OUROBOROS_MCP_TRANSPORT` | how the MCP server talks: `stdio` (default), `sse` or `streamable-http`. Any other value — the server refuses to start and tells you which ones exist |

The tool has no other variables
([`ouroboros/runtime.py:62`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L62),
[`ouroboros/mcp/server.py:935`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/mcp/server.py#L935)).

## Next

- [Getting started](getting-started.md) — every command and the order of work
- [Trace code you did not write](trace-existing-code.md) — the first real run, step by step
- [Limits](limits.md) — read before conclusions grow out of a trace
