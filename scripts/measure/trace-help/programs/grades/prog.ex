defmodule Grades do
  @moduledoc "Оценки: надбавка, буква, подсчёт прошедших."

  def to_number(text) do
    String.to_integer(String.trim(text))
  end

  def bonus(score) do
    if score > 95 do
      5
    else
      0
    end
  end

  def curve(score) do
    score + bonus(score)
  end

  def letter(score) do
    cond do
      score >= 90 -> "A"
      score >= 75 -> "B"
      score >= 60 -> "C"
      true -> "F"
    end
  end

  def passed(mark) do
    mark != "F"
  end

  def honors(marks) do
    Enum.count(marks, fn m -> m == "A" end)
  end

  def run(texts) do
    marks = Enum.map(texts, fn t -> letter(curve(to_number(t))) end)
    passing = Enum.count(marks, fn m -> passed(m) end)
    Enum.join(marks, ",") <> " прошли:" <> Integer.to_string(passing)
  end
end
