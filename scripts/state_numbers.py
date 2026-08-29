"""Держит числа состояния в документации равными измеренным.

Зачем. Числа, вписанные руками, расходятся с делом молча. В этом дереве уже
случилось дважды: `ARCHITECTURE.md` обещал 91 % покрытия — на 9,5 пункта выше
потолка, который был недостижим даже при идеальных проверках, — и «~105 тестов»
при 164 настоящих. Ни то, ни другое никто не пересчитал после того, как вписал.
`README.md` тем временем говорил «167 из 167», когда проверок было уже 440.

Как. В страницах стоят пометки вида::

    <!--state:tests-->440<!--/state-->

Внутри пометки текст принадлежит машине. `--measure` прогоняет проверки с
подсчётом покрытия, кладёт измеренное в `docs/state.json` и переписывает
пометки. Без доводов идёт сверка: пометки сравниваются с `docs/state.json`, а
дешёвые числа (сколько проверок, сколько средств, какие языки) пересчитываются
заново прямо сейчас. Разошлось — отказ с указанием, что стало.

Сверка стоит секунды и потому висит в `scripts/qa.sh`. Полный замер идёт минуты
и потому вызывается руками, когда числа меняются.

Запуск::

    uv run python scripts/state_numbers.py            # сверить
    uv run python scripts/state_numbers.py --measure   # замерить и переписать
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: Куда кладётся измеренное. Лежит в дереве, потому что сверка должна работать
#: без повторного прогона, а разница в `git diff` — показывать, что сдвинулось.
STATE_FILE = ROOT / "docs" / "state.json"

#: Страницы, в которых стоят пометки.
PAGES = ["README.md", "ARCHITECTURE.md"]

MARK = re.compile(r"<!--state:([a-z_]+)-->(.*?)<!--/state-->", re.DOTALL)


def measure() -> dict[str, Any]:
    """Прогоняет проверки с подсчётом покрытия и собирает числа."""

    print("== прогоняю проверки с подсчётом покрытия (это небыстро) ==")
    proc = subprocess.run(
        ["uv", "run", "pytest", "--cov", "--cov-report=json:.coverage.json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout[-2000:])
    if proc.returncode != 0:
        raise SystemExit("проверки не прошли — числа состояния не обновляю")

    # Итог ищем по всему выводу: с включённым подсчётом покрытия последней
    # строкой идёт сообщение о записи файла, а не «N passed».
    found = re.findall(r"(\d+) passed", proc.stdout)
    if not found:
        raise SystemExit(f"не разобрал итог прогона: {proc.stdout.strip()[-300:]!r}")
    tests = int(found[-1])

    with (ROOT / ".coverage.json").open(encoding="utf-8") as fh:
        cov = json.load(fh)
    percent = cov["totals"]["percent_covered"]

    return {
        "tests": tests,
        "coverage_percent": round(percent),
        "coverage_exact": round(percent, 2),
        "mcp_tools": tool_count(),
        "languages": languages(),
        "measured_with": "pytest --cov (statements and branches)",
    }


def tool_count() -> int:
    """Сколько средств объявляет сервер — из снятого живьём справочника."""

    path = ROOT / "docs" / "mcp-tools.json"
    with path.open(encoding="utf-8") as fh:
        return int(json.load(fh)["declared_tool_count"])


def languages() -> int:
    proc = subprocess.run(
        ["uv", "run", "ouroboros", "languages"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return len(json.loads(proc.stdout)["languages"])


def collected_tests() -> int:
    """Сколько проверок собирается — без их прогона, это быстро."""

    proc = subprocess.run(
        ["uv", "run", "pytest", "--collect-only"],
        cwd=ROOT, capture_output=True, text=True,
    )
    # pytest печатает итог сбора двумя разными способами: обычно строкой
    # «N tests collected», а при двойной тишине (в pyproject уже стоит `-q`,
    # и второй `-q` приходит отсюда) — построчно по файлам, «путь: N».
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if m is not None:
        return int(m.group(1))
    per_file = re.findall(r"^\S+\.py: (\d+)$", proc.stdout, re.MULTILINE)
    if per_file:
        return sum(int(n) for n in per_file)
    raise SystemExit(f"не разобрал сбор проверок: {proc.stdout.strip()[-300:]!r}")


def apply_marks(state: dict[str, Any]) -> list[str]:
    """Переписывает пометки в страницах. Возвращает список изменённых."""

    changed = []
    for name in PAGES:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")

        # `page=name` связывает имя страницы СЕЙЧАС, а не при вызове: без этого
        # замыкание смотрело бы на переменную цикла, и в сообщении об ошибке
        # стояла бы последняя страница, а не та, в которой беда.
        def swap(m: re.Match[str], page: str = name) -> str:
            key = m.group(1)
            if key not in state:
                raise SystemExit(f"{page}: пометка {key!r} — такого числа не измеряют")
            return f"<!--state:{key}-->{state[key]}<!--/state-->"

        new = MARK.sub(swap, text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(name)
    return changed


def check(state: dict[str, Any]) -> list[str]:
    """Сверяет пометки с измеренным и с тем, что можно пересчитать сейчас."""

    problems: list[str] = []

    # Дешёвое пересчитываем заново: если проверок стало больше, а замер старый,
    # надо сказать именно это, а не сверять две одинаково устаревшие записи.
    now = collected_tests()
    if now != state.get("tests"):
        problems.append(
            f"проверок сейчас {now}, а в docs/state.json записано "
            f"{state.get('tests')} — прогоните --measure"
        )
    tools = tool_count()
    if tools != state.get("mcp_tools"):
        problems.append(
            f"средств в справочнике {tools}, а в docs/state.json {state.get('mcp_tools')}"
        )

    for name in PAGES:
        text = (ROOT / name).read_text(encoding="utf-8")
        for m in MARK.finditer(text):
            key, shown = m.group(1), m.group(2)
            if key not in state:
                problems.append(f"{name}: пометка {key!r} — такого числа не измеряют")
            elif shown != str(state[key]):
                problems.append(
                    f"{name}: в пометке {key} стоит {shown!r}, измерено {state[key]!r}"
                )
    return problems


def main() -> int:
    if "--measure" in sys.argv:
        state = measure()
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        changed = apply_marks(state)
        print(f"\nизмерено: {json.dumps(state, ensure_ascii=False)}")
        print(f"записано в {STATE_FILE.relative_to(ROOT)}")
        print("страницы обновлены: " + (", ".join(changed) if changed else "нечего менять"))
        return 0

    if not STATE_FILE.exists():
        print(f"нет {STATE_FILE.relative_to(ROOT)} — прогоните с --measure")
        return 1
    with STATE_FILE.open(encoding="utf-8") as fh:
        state = json.load(fh)

    problems = check(state)
    if problems:
        print("Числа состояния разошлись с измеренным:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nПочинка: uv run python scripts/state_numbers.py --measure")
        return 1
    print(f"Числа состояния сходятся с измеренным ({state['tests']} проверок, "
          f"покрытие {state['coverage_percent']} %).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
