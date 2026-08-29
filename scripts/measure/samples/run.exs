[m, n] = System.argv()
mod = String.to_atom("Elixir." <> m)
IO.puts(mod.run(String.to_integer(n)))
