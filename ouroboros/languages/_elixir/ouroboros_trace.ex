defmodule Ouroboros.Trace do
  @moduledoc """
  Function-boundary logging for Elixir — the BEAM-native analogue of the Python
  decorator. `use Ouroboros.Trace` overrides `def`/`defp` so every clause is
  wrapped INDEPENDENTLY (no clause regrouping): guards and default args pass
  through verbatim, arguments are captured from `binding()`, and the body runs
  inside a try so normal returns, raises, throws and exits are all logged.

  Each call appends two JSONL lines (SPEC.md) — `{"p":"in",...}` on entry and
  `{"p":"out",...}` on completion, paired by `id` — to the file named by
  OUROBOROS_DEBUG_INFO (default "debug.info"). Each line is bounded well under
  PIPE_BUF and written with a single O_APPEND write, so concurrent processes on
  the BEAM cannot interleave a record.

  Compile ordering: this module must be compiled/loaded BEFORE any module that
  `use`s it (the macro expands at compile time).
  """
  import Bitwise

  defmacro __using__(_opts) do
    quote do
      import Kernel, except: [def: 2, defp: 2]
      import Ouroboros.Trace, only: [def: 2, defp: 2]
    end
  end

  defmacro def(call, expr \\ nil), do: wrap(:def, call, expr)
  defmacro defp(call, expr \\ nil), do: wrap(:defp, call, expr)

  defp fname({:when, _, [head | _]}), do: fname(head)
  defp fname({name, _, _}) when is_atom(name), do: name

  defp wrap(kind, call, do: body) do
    name = fname(call)

    quote do
      Kernel.unquote(kind)(unquote(call)) do
        ctx = Ouroboros.Trace.__enter__(unquote(name), binding())

        try do
          res = Ouroboros.Trace.__run__(fn -> unquote(body) end)
          Ouroboros.Trace.__leave__(ctx, res)
          res
        rescue
          e ->
            Ouroboros.Trace.__raise__(ctx, :error, e)
            reraise e, __STACKTRACE__
        catch
          kind, value ->
            Ouroboros.Trace.__raise__(ctx, kind, value)
            :erlang.raise(kind, value, __STACKTRACE__)
        end
      end
    end
  end

  # Function head (default-arg head) or complex block (rescue/after/...) — forward verbatim.
  defp wrap(kind, call, nil), do: quote(do: Kernel.unquote(kind)(unquote(call)))
  defp wrap(kind, call, expr), do: quote(do: Kernel.unquote(kind)(unquote(call), unquote(expr)))

  # ---- runtime sink --------------------------------------------------------

  # Per-value ceiling, in bytes — the same 200 the Python (reprlib) and JS
  # helpers use, so one huge argument costs the same in every language.
  @max_value 200
  # Per-record ceiling, in bytes. SPEC.md §1 promises each record is written with
  # a single append and stays under PIPE_BUF (4096 on Linux), which is what lets
  # several processes share one debug.info: a line longer than that is torn by
  # the kernel, and the parser then counts both halves as malformed and drops
  # them — data lost without a warning.
  @max_record 4096

  def __enter__(name, binding) do
    # Values only, comma-separated. SPEC.md splits the two fields: `a` carries
    # positional values, `k` carries name=value pairs (and the BEAM, having no
    # kwargs, emits `k` empty). Writing "a=1, b=2" into `a` put names in the
    # field that must not hold them, and made the Elixir trace uncomparable with
    # the Python and JS ones for the same call.
    args = Enum.map_join(binding, ", ", fn {_k, v} -> rep(v) end)

    ctx = %{
      name: name,
      args: args,
      uuid: uuid(),
      # `th` is "<os pid>.<beam process>" — both halves, like every other
      # backend: the OS pid alone cannot tell two BEAM processes apart, and the
      # BEAM process alone cannot tell two nodes on one debug.info apart.
      th: "#{System.pid()}.#{inspect(self())}",
      # `ci` is a CPU index, and the BEAM will not tell us one. The scheduler id
      # that used to sit here is a different number that merely looks like a CPU
      # (schedulers migrate), so a reader comparing it with the other languages'
      # `ci` was comparing two unrelated things. -1 is the contract's "unknown".
      ci: -1,
      started: now_iso()
    }

    # `p:in` entry event, then start the monotonic duration clock AFTER it
    # (exclude the entry-write overhead from the measured duration).
    emit_entry(ctx)
    Map.put(ctx, :t0, System.monotonic_time(:nanosecond))
  end

  # Run the wrapped body behind this opaque-closure boundary. A clause whose body
  # always raises (`def f(...), do: raise(...)`) has type none(); Elixir's
  # set-theoretic type checker propagates that into any consumer, so the naive
  # `res = unquote(body)` produced a "pattern will never match (none())" warning
  # the ORIGINAL un-instrumented clause never had. Passing the body as a closure
  # to __run__ confines the none() to the closure: __run__ analyses `body` as a
  # generic 0-arity fun (return dynamic()), so the call site sees no none() flow
  # and the instrumentation stays transparent (clean under `--warnings-as-errors`).
  def __run__(body) when is_function(body, 0), do: body.()

  def __leave__(ctx, result), do: emit(ctx, "r", rep(result))

  # `x` is "<Type>: <message>" (SPEC.md). A rescued exception knows both; a
  # thrown or exited value has no type of its own, so the catch kind stands in.
  def __raise__(ctx, :error, e) when is_exception(e),
    do: emit(ctx, "x", "#{inspect(e.__struct__)}: #{cap(Exception.message(e), @max_value)}")

  def __raise__(ctx, kind, value), do: emit(ctx, "x", "#{kind}: " <> rep(value))

  defp path, do: System.get_env("OUROBOROS_DEBUG_INFO") || "debug.info"

  # Local ISO-8601 with millisecond precision, matching the Python helper's
  # datetime.now().isoformat(timespec="milliseconds"). NaiveDateTime.local_now/0
  # resolves to whole seconds, which silently dropped the millisecond field the
  # schema promises — enough to make two calls in the same second unorderable.
  defp now_iso do
    ms = System.os_time(:millisecond)
    {{y, mo, d}, {h, mi, s}} = :calendar.system_time_to_local_time(ms, :millisecond)

    :io_lib.format("~4..0B-~2..0B-~2..0BT~2..0B:~2..0B:~2..0B.~3..0B",
      [y, mo, d, h, mi, s, Integer.mod(ms, 1000)])
    |> to_string()
  end

  # `p:in` entry event. `a` carries the rendered args; `k` is "" for schema
  # parity with the kwargs-bearing languages (the BEAM has no kwargs).
  defp emit_entry(ctx) do
    write(fn a ->
      ~s({"p":"in","t":#{jstr(ctx.started)},"id":#{jstr(ctx.uuid)},) <>
        ~s("ci":#{ctx.ci},"th":#{jstr(ctx.th)},) <>
        ~s("fn":#{jstr(ctx.name)},"a":#{jstr(a)},"k":""}\n)
    end, ctx.args)
  end

  # `p:out` completion event. `key` is "r" (result) or "x" (raised); `d` is the
  # real per-call duration in seconds (a JSON number).
  defp emit(ctx, key, outcome) do
    secs = (System.monotonic_time(:nanosecond) - ctx.t0) / 1_000_000_000
    duration = :erlang.float_to_binary(secs, decimals: 6)

    write(fn o ->
      ~s({"p":"out","id":#{jstr(ctx.uuid)},"fn":#{jstr(ctx.name)},) <>
        ~s("#{key}":#{jstr(o)},"d":#{duration}}\n)
    end, outcome)
  end

  # Build the line and append it. Exactly one field per record varies in length,
  # so halving that one until the whole line fits is enough to keep the
  # single-append promise — and it handles both shapes of overflow: one enormous
  # argument, or thirty ordinary ones.
  defp write(build, value), do: File.write(path(), bounded(build, value), [:append])

  defp bounded(build, value) do
    line = build.(value)

    if byte_size(line) <= @max_record or byte_size(value) == 0 do
      line
    else
      bounded(build, halve(value))
    end
  end

  defp halve(v) do
    base = String.replace_suffix(v, "…", "")

    if byte_size(base) == 0 do
      ""
    else
      utf8_prefix(base, div(byte_size(base), 2)) <> "…"
    end
  end

  defp rep(v), do: cap(inspect(v, limit: 10, printable_limit: @max_value), @max_value)

  # Emit `v` (atom or binary) as a JSON string literal, escaping the mandatory
  # control chars plus \ and ".
  defp jstr(v) when is_atom(v), do: jstr(Atom.to_string(v))

  defp jstr(v) when is_binary(v) do
    inner =
      v
      |> String.replace("\\", "\\\\")
      |> String.replace("\"", "\\\"")
      |> String.replace("\n", "\\n")
      |> String.replace("\r", "\\r")
      |> String.replace("\t", "\\t")

    "\"" <> inner <> "\""
  end

  defp cap(s, max) when byte_size(s) > max, do: utf8_prefix(s, max) <> "…"
  defp cap(s, _max), do: s

  # Longest prefix of at most `n` BYTES that is still valid UTF-8. Slicing on a
  # raw byte count can cut a character in half, and half a character makes the
  # whole JSON line undecodable — losing the record the cap exists to save.
  defp utf8_prefix(s, n) when byte_size(s) <= n, do: s

  defp utf8_prefix(_s, n) when n <= 0, do: ""

  defp utf8_prefix(s, n) do
    candidate = binary_part(s, 0, n)
    if String.valid?(candidate), do: candidate, else: utf8_prefix(s, n - 1)
  end

  defp uuid do
    <<a::32, b::16, c::16, d::16, e::48>> = :crypto.strong_rand_bytes(16)
    c = bor(band(c, 0x0FFF), 0x4000)
    d = bor(band(d, 0x3FFF), 0x8000)

    :io_lib.format("~8.16.0b-~4.16.0b-~4.16.0b-~4.16.0b-~12.16.0b", [a, b, c, d, e])
    |> to_string()
  end
end
