# Packaging & deployment

Two distributable artifacts of the Ouroboros-Logger MCP server.

## A. Single-file binary (`dist/ouroboros`)

One self-contained executable (~47 MB) — **no Python install required**. The
libclang native library is bundled, so **Python works fully standalone** and C
needs only a C compiler on the host (which anyone instrumenting C has anyway).

```bash
uv run pyinstaller packaging/ouroboros.spec --noconfirm
./dist/ouroboros languages              # -> python, javascript, c, cpp, elixir
./dist/ouroboros mcp                     # MCP server over stdio
echo 'int add(int a,int b){return a+b;}' | ./dist/ouroboros wrap-snippet -l c
```

| Backend | Standalone in the binary? |
|---------|---------------------------|
| Python  | ✅ yes (stdlib only) |
| C        | libclang is bundled, but the range emitter is built on the host at
             first use, so `cc` is needed to wrap as well as to `execute`. Set
             `OUROBOROS_CLANG_EMITTER` to a prebuilt emitter to skip the build. |
| C++      | needs `g++`/`clang++` on the host (include-path discovery + compile) |
| JS/TS    | needs `node` on the host |
| Elixir   | needs `elixir`/`erlang` on the host |

Use this for a lightweight, easy-to-ship **Python+C edition**, or as the engine
on a host that already has the other toolchains. MCP client config:

```json
{ "mcpServers": { "ouroboros": { "command": "/path/to/ouroboros", "args": ["mcp"] } } }
```

## B. Full multi-language image (`packaging/Dockerfile`)

Every toolchain baked in (node+@babel, clang/libclang+gcc/g++, erlang+elixir+mix,
git) so all five backends and `execute` work out of the box.

```bash
docker build -t ouroboros-logger -f packaging/Dockerfile .
docker run --rm -i ouroboros-logger        # MCP server over stdio
```

MCP client config:

```json
{ "mcpServers": { "ouroboros": { "command": "docker", "args": ["run","--rm","-i","ouroboros-logger"] } } }
```

Pin the `elixir:` base tag to match your deployment's BEAM version if you
instrument Elixir for a specific target (e.g. OTP 29 for the ROS fork).

## Server requirements summary

- **Core:** Python ≥ 3.12, `git`. Process is light at idle; per-wrap cost is the
  emitter subprocess start — node/elixir ≈ 0.1–0.5 s, the C/C++ emitter ≈ 5 ms
  (a native binary: ~1 ms to spawn, ~4 ms to open libclang), plus the parse
  itself. Linux/macOS (Windows untested).
- **Per language:** see the table above — the parser toolchain to *wrap*, the
  language runtime/compiler to *execute*.
- **Disk:** our code ~6 MB; libclang ~60 MB; node ~70 MB; erlang+elixir
  ~250–300 MB. Full image ≈ 0.8–1 GB.

## Security (read before hosting)

The `execute` tool spawns processes and the sandbox writes files — it runs
**agent-provided code**. For a single local user this is the same trust as their
own shell. For a **hosted / multi-tenant** service this is untrusted code
execution: run **one container per session**, with CPU/memory/PID/disk limits,
no outbound network, and a read-only base FS outside the draft dir. Do not run
the bare binary as a shared network service without that isolation.

## Licensing (redistribution)

All bundled/relied-on components are permissive — libclang/LLVM (Apache-2.0),
node & @babel (MIT), Elixir & Erlang/OTP (Apache-2.0), MCP SDK (MIT), Python
(PSF). No GPL in the bundled path (we call the host's `gcc`, don't ship it; bundle
`clang`/Apache if a compiler must be included). The product is freely sellable
with attribution.
