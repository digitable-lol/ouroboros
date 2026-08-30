#!/usr/bin/env python3
"""Готовит подопытные программы: обмазывает, собирает, запускает, сверяет ключ.

На выходе — файл `случаи.json`: на каждую программу исходник, команда запуска,
то, что программа напечатала, и трасса. Плюс проверка: правильные ответы,
записанные руками в meta.json, сверяются с тем, что достаётся из трассы. При
расхождении команда падает — значит, ключ или программа неверны.

Запуск: build.py <рабочий каталог> <куда положить случаи.json>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE / "programs"


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    """Запускает команду и возвращает её вывод; падает, если команда упала."""

    full = dict(os.environ)
    if env:
        full.update(env)
    done = subprocess.run(cmd, cwd=cwd, env=full, capture_output=True, text=True)
    if done.returncode != 0:
        print(f"упало: {' '.join(cmd)}\n{done.stdout}\n{done.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return done.stdout


def ouroboros(*args: str) -> None:
    run(["ouroboros", *args], Path.cwd())


def read_trace(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("{"):
            out.append(json.loads(line))
    return out


def calls(trace: list[dict], fn: str) -> list[dict]:
    return [r for r in trace if r.get("p") == "in" and r.get("fn") == fn]


def returns(trace: list[dict], fn: str) -> list[str]:
    """Возвраты вызовов функции в порядке ВХОДА в них, а не выхода."""

    by_id = {r["id"]: r for r in trace if r.get("p") == "out"}
    out = []
    for rec in calls(trace, fn):
        done = by_id.get(rec["id"])
        if done is not None and "r" in done:
            out.append(str(done["r"]))
    return out


def from_trace(trace: list[dict], check: dict) -> str:
    """Достаёт из трассы ту же величину, о которой спрашивает вопрос."""

    kind = check["вид"]
    if kind == "число":
        return str(len(calls(trace, check["fn"])))
    if kind == "звался":
        return "да" if calls(trace, check["fn"]) else "нет"
    if kind == "возврат":
        values = returns(trace, check["fn"])
        n = check["n"]
        return values[n - 1 if n > 0 else n]
    if kind == "довод":
        recs = calls(trace, check["fn"])
        n = check["n"]
        raw = recs[n - 1 if n > 0 else n].get("a", "")
        return raw.split(", ")[check["поле"]]
    if kind == "бросил":
        for r in trace:
            if r.get("p") == "out" and "x" in r:
                return str(r["fn"])
        return "нет"
    raise ValueError(kind)


def tidy(text: str) -> str:
    """Общий вид для сравнения: без кавычек, без края, в нижнем регистре."""

    text = text.strip().strip("«»\"'`").strip()
    return " ".join(text.lower().split())


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def prepare(name: str, work: Path, args: list[str] | None = None,
            questions: list[dict] | None = None, into: str | None = None) -> dict | None:
    """Готовит одну программу. Возвращает случай или None, если языка нет.

    `args` заменяет доводы из meta.json, `questions` — вопросы, `into` — имя
    подкаталога. Это нужно длинному опыту, который запускает те же программы на
    большом входе и задаёт вопросы, взятые из полной трассы.
    """

    meta = json.loads((PROGRAMS / name / "meta.json").read_text(encoding="utf-8"))
    lang = meta["язык"]
    src_name = meta["файл"]
    src = (PROGRAMS / name / src_name).read_text(encoding="utf-8")
    if args is None:
        args = [str(a) for a in meta["доводы"]]
    if questions is None:
        questions = meta["вопросы"]

    need = {"python": "python3", "javascript": "node", "c": "gcc",
            "cpp": "g++", "go": "go", "elixir": "elixirc"}[lang]
    if not have(need):
        print(f"-- {name} пропущен: нет {need}")
        return None

    d = work / (into or name)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    trace_path = d / "debug.info"
    env = {"OUROBOROS_DEBUG_INFO": str(trace_path)}

    plain_dir = d / "plain"
    plain_dir.mkdir()
    shutil.copy(PROGRAMS / name / src_name, plain_dir / src_name)
    shutil.copy(PROGRAMS / name / src_name, d / src_name)
    run(["ouroboros", "wrap-file", str(d / src_name)], d)

    show_src = src
    if lang == "python":
        plain_cmd = ["python3", src_name, *args]
        wrapped_cmd = ["python3", src_name, *args]
        shown = f"python3 {src_name} " + " ".join(args)
    elif lang == "javascript":
        plain_cmd = ["node", src_name, *args]
        wrapped_cmd = ["node", src_name, *args]
        shown = f"node {src_name} " + " ".join(args)
    elif lang == "c":
        run(["gcc", "-O0", "-o", "prog", src_name], plain_dir)
        run(["gcc", "-O0", "-o", "prog", src_name], d)
        plain_cmd = ["./prog", *args]
        wrapped_cmd = ["./prog", *args]
        shown = "./prog " + " ".join(args)
    elif lang == "cpp":
        run(["g++", "-std=c++17", "-O0", "-o", "prog", src_name], plain_dir)
        run(["g++", "-std=c++17", "-O0", "-o", "prog", src_name], d)
        plain_cmd = ["./prog", *args]
        wrapped_cmd = ["./prog", *args]
        shown = "./prog " + " ".join(args)
    elif lang == "go":
        run(["go", "build", "-o", "prog", src_name], plain_dir)
        run(["go", "build", "-o", "prog", src_name, "ouroboros_runtime.go"], d)
        plain_cmd = ["./prog", *args]
        wrapped_cmd = ["./prog", *args]
        shown = "./prog " + " ".join(args)
    else:  # elixir
        helper = HERE.parents[2] / "ouroboros" / "languages" / "_elixir" / "ouroboros_trace.ex"
        shutil.copy(helper, d / "ouroboros_trace.ex")
        shutil.copy(PROGRAMS / name / "run.exs", d / "run.exs")
        shutil.copy(PROGRAMS / name / "run.exs", plain_dir / "run.exs")
        run(["elixirc", "-o", "ebin", "ouroboros_trace.ex", src_name], d)
        run(["elixirc", "-o", "ebin", src_name], plain_dir)
        plain_cmd = ["elixir", "-pa", "ebin", "run.exs"]
        wrapped_cmd = ["elixir", "-pa", "ebin", "run.exs"]
        shown = "elixir -pa ebin run.exs"
        show_src = src + "\n\n%% файл run.exs %%\n" + \
            (PROGRAMS / name / "run.exs").read_text(encoding="utf-8")

    plain_out = run(plain_cmd, plain_dir)
    if trace_path.exists():
        trace_path.unlink()
    wrapped_out = run(wrapped_cmd, d, env)
    if plain_out != wrapped_out:
        print(f"{name}: обмазанная программа печатает не то же самое!\n"
              f"без: {plain_out!r}\nс:   {wrapped_out!r}", file=sys.stderr)
        raise SystemExit(1)

    trace = read_trace(trace_path)
    for q in questions:
        got = tidy(from_trace(trace, q["проверка"]))
        want = [tidy(a) for a in q["ответ"]]
        if got not in want:
            print(f"{q['id']}: ключ {want} не сошёлся с трассой {got!r}", file=sys.stderr)
            raise SystemExit(1)

    print(f"-- {name} ({lang}): {len(trace)} строк трассы, ключ сошёлся")
    return {
        "имя": name,
        "язык": lang,
        "исходник": show_src,
        "команда": shown,
        "вывод": plain_out,
        "трасса": trace_path.read_text(encoding="utf-8"),
        "вопросы": questions,
    }


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    cases = []
    for name in sorted(p.name for p in PROGRAMS.iterdir() if p.is_dir()):
        case = prepare(name, work)
        if case is not None:
            cases.append(case)
    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(c["вопросы"]) for c in cases)
    print(f"== программ {len(cases)}, вопросов {total} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
