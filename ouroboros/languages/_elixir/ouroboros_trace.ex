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
  @max 240

  def __enter__(name, binding) do
    args =
      binding
      |> Enum.map_join(", ", fn {k, v} -> "#{k}=#{rep(v)}" end)
      |> cap()

    ctx = %{
      name: name,
      args: args,
      uuid: uuid(),
      # Concurrency identity: `th` is the BEAM process (inspect(self())), `ci` the
      # scheduler running it (≈ logical CPU) — the per-process trace view.
      th: inspect(self()),
      ci: :erlang.system_info(:scheduler_id),
      started: NaiveDateTime.to_iso8601(NaiveDateTime.local_now())
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

  def __leave__(ctx, result), do: emit(ctx, "r", cap(rep(result)))
  def __raise__(ctx, kind, value), do: emit(ctx, "x", "#{kind}: " <> cap(rep(value)))

  defp path, do: System.get_env("OUROBOROS_DEBUG_INFO") || "debug.info"

  # `p:in` entry event. `a` carries the rendered args; `k` is "" for schema
  # parity with the kwargs-bearing languages (the BEAM has no kwargs). `ci`/`th`
  # are the scheduler id and the process (inspect(self())) — concurrency identity.
  defp emit_entry(ctx) do
    line =
      ~s({"p":"in","t":#{jstr(ctx.started)},"id":#{jstr(ctx.uuid)},) <>
        ~s("ci":#{ctx.ci},"th":#{jstr(ctx.th)},) <>
        ~s("fn":#{jstr(ctx.name)},"a":#{jstr(ctx.args)},"k":""}\n)

    File.write(path(), line, [:append])
  end

  # `p:out` completion event. `key` is "r" (result) or "x" (raised); `d` is the
  # real per-call duration in seconds (a JSON number).
  defp emit(ctx, key, outcome) do
    secs = (System.monotonic_time(:nanosecond) - ctx.t0) / 1_000_000_000
    duration = :erlang.float_to_binary(secs, decimals: 6)

    line =
      ~s({"p":"out","id":#{jstr(ctx.uuid)},"fn":#{jstr(ctx.name)},) <>
        ~s("#{key}":#{jstr(outcome)},"d":#{duration}}\n)

    File.write(path(), line, [:append])
  end

  defp rep(v), do: inspect(v, limit: 10, printable_limit: 200)

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

  defp cap(s) when byte_size(s) > @max, do: binary_part(s, 0, @max) <> "…"
  defp cap(s), do: s

  defp uuid do
    <<a::32, b::16, c::16, d::16, e::48>> = :crypto.strong_rand_bytes(16)
    c = bor(band(c, 0x0FFF), 0x4000)
    d = bor(band(d, 0x3FFF), 0x8000)

    :io_lib.format("~8.16.0b-~4.16.0b-~4.16.0b-~4.16.0b-~12.16.0b", [a, b, c, d, e])
    |> to_string()
  end
end
