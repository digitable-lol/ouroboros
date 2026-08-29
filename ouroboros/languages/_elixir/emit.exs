# Ouroboros Elixir range-emitter.
#
# Reads Elixir source on stdin, parses it with the real Elixir parser, and prints
# the (line, column) of the `do` keyword of every block-form `defmodule`. The
# Python backend uses these to splice `use Ouroboros.Trace` into each module.
#
# Output: first line "ok" then "<line> <column>" per module, or "err: <message>".
# Keyword-form modules (`defmodule Foo, do: ...`) have no do-block and are skipped.

src = IO.read(:stdio, :eof)

case Code.string_to_quoted(src, columns: true, token_metadata: true) do
  {:ok, ast} ->
    {_ast, positions} =
      Macro.prewalk(ast, [], fn
        {:defmodule, meta, _args} = node, acc ->
          case meta[:do] do
            [line: l, column: c] -> {node, [{l, c} | acc]}
            _ -> {node, acc}
          end

        node, acc ->
          {node, acc}
      end)

    IO.puts("ok")
    positions |> Enum.reverse() |> Enum.each(fn {l, c} -> IO.puts("#{l} #{c}") end)

  {:error, {meta, msg, token}} ->
    detail = if is_binary(msg), do: msg, else: inspect(msg)
    IO.puts("err: #{detail} #{inspect(token)} at #{inspect(meta)}")

  {:error, reason} ->
    IO.puts("err: #{inspect(reason)}")
end
