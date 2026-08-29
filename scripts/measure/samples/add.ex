defmodule Sample do
  def add(a, b), do: a + b

  def ratio(_a, 0), do: raise(ArithmeticError, "деление на ноль")
  def ratio(a, b), do: a / b

  def run(n) do
    total = Enum.reduce(0..(n - 1), 0, fn i, acc -> add(acc, i) end)

    try do
      ratio(1, 0)
    rescue
      _ -> :ok
    end

    total
  end
end
