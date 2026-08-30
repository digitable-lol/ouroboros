defmodule Ledger do
  @moduledoc "Проводки: приход и расход, сбор за крупные суммы."

  def parse(line) do
    [kind, amount] = String.split(line, ":")
    {kind, String.to_integer(amount)}
  end

  def sign(kind) do
    case kind do
      "приход" -> 1
      "расход" -> -1
      _ -> raise ArgumentError, "неизвестная проводка: " <> kind
    end
  end

  def fee(amount) do
    if amount > 1000 do
      div(amount, 100)
    else
      0
    end
  end

  def apply_entry(balance, kind, amount) do
    balance + sign(kind) * amount - fee(amount)
  end

  def audit(balance) do
    balance
  end

  def run(lines) do
    Enum.reduce(lines, 0, fn line, acc ->
      {kind, amount} = parse(line)
      apply_entry(acc, kind, amount)
    end)
  end
end
