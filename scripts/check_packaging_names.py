"""Сверяет имена в рецепте Homebrew и в плагине asdf с тем, что кладёт пакет.

Зачем. В формуле Homebrew однажды стояла строка

    assert_path_exists bin/"ouroboros-mcp-router"

Такого имени у пакета не было никогда. Строка выглядела как проверка, но ничего
не проверяла: до неё не доходило дело, а глазами она читается как правдоподобная.
Имена команд заводятся ровно в одном месте — в `[project.scripts]` файла
`pyproject.toml`, подкоманды — в разборщике `ouroboros/cli.py`. Всё остальное,
что называет имя, обязано с ними сходиться, и сверяет это машина.

Чего здесь нет. Самой установки: она требует Homebrew, asdf и сети и проверяется
прогоном, а не этой проверкой (см. `docs/install.md`). Здесь только сверка имён —
то есть ровно та ошибка, которая переживает любой прогон, пока до неё не дойдёт
очередь.

    uv run python scripts/check_packaging_names.py
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FORMULA = ROOT / "packaging" / "homebrew" / "ouroboros.rb"
ASDF_INSTALL = ROOT / "packaging" / "asdf" / "bin" / "install"


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _scripts() -> set[str]:
    """Имена команд, которые пакет действительно кладёт в bin/ при установке."""
    return set(_pyproject()["project"]["scripts"])


def _subcommands() -> set[str]:
    """Подкоманды, которые действительно понимает `ouroboros`."""
    from ouroboros import cli

    names: set[str] = set()
    for action in cli._build_parser()._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names.update(choices)
    return names


def _formula_body() -> str:
    """Тело рецепта без строк-примечаний.

    Примечания выброшены нарочно: в них рецепт рассказывает и о том, чего у
    пакета нет, — например о том самом выдуманном имени. Строка внутри caveats,
    начинающаяся с решётки, тоже была бы выброшена; таких сейчас нет, а если
    появятся — проверка их пропустит, но не соврёт.
    """
    lines = FORMULA.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def main() -> int:
    beda: list[str] = []
    scripts = _scripts()
    body = _formula_body()

    # 1. Всё, к чему рецепт обращается как к вынесенной команде.
    #    bin/"ouroboros" — но не opt_bin/"python3.12" и не libexec/"bin/python".
    for obrazec in (r'(?<![A-Za-z_])bin/"([^"]+)"', r'#\{bin\}/([A-Za-z0-9._-]+)'):
        found = set(re.findall(obrazec, body))
        if not found:
            beda.append(f"в рецепте не нашлось ни одного имени по образцу {obrazec!r} — "
                        "образец устарел, проверка перестала что-либо проверять")
            continue
        for name in sorted(found - scripts):
            beda.append(f"рецепт зовёт команду {name!r}, а пакет её не кладёт; "
                        f"пакет кладёт только {sorted(scripts)}")

    # 2. Наружу должна выноситься каждая команда пакета: рецепт выносит их
    #    образцом Dir[libexec/"bin/<начало>*"].
    globs = re.findall(r'Dir\[libexec/"bin/([^"]*)\*"\]', body)
    if not globs:
        beda.append('в рецепте нет строки bin.install_symlink Dir[libexec/"bin/…*"]')
    else:
        for name in sorted(scripts):
            if not any(name.startswith(g) for g in globs):
                beda.append(f"команда {name!r} не попадает ни под один образец {globs} — "
                            "после установки её не будет на PATH")

    # 3. Подкоманды, которые рецепт зовёт в test do и советует в caveats.
    known = _subcommands()
    used = set(re.findall(r"\bouroboros ([a-z][a-z-]+)\b", body))
    used |= set(re.findall(r'bin/"ouroboros",\s*"([a-z][a-z-]+)"', body))
    if not used:
        beda.append("в рецепте не нашлось ни одного вызова подкоманды — образец устарел")
    for name in sorted(used - known):
        beda.append(f"рецепт зовёт подкоманду {name!r}, которой нет; есть {sorted(known)}")

    # 4. Имя команды в настройке сервера MCP, которую печатают caveats.
    commands = set(re.findall(r'"command":\s*"([A-Za-z0-9._-]+)"', body))
    if not commands:
        beda.append("в caveats нет настройки MCP с полем command")
    for name in sorted(commands - scripts):
        beda.append(f"в настройке MCP имя {name!r}, которого у пакета нет")

    # 5. Тег в адресе архива — это версия пакета.
    version = _pyproject()["project"]["version"]
    tags = set(re.findall(r"/tags/v([0-9][0-9A-Za-z.]*)\.tar\.gz", body))
    if not tags:
        beda.append("в рецепте не нашлось адреса архива с тегом версии")
    elif tags != {version}:
        beda.append(f"рецепт тянет версии {sorted(tags)}, а пакет сейчас {version}")

    # 6. Плагин asdf выносит наружу ровно команды пакета. Список в нём явный:
    #    вместе с пакетом в окружение приезжают команды зависимостей (httpx,
    #    uvicorn, dotenv), и asdf сделал бы обёртку на каждую.
    text = ASDF_INSTALL.read_text(encoding="utf-8")
    spisok = re.search(r"for name in ([^;\n]+); do", text)
    if not spisok:
        beda.append("в packaging/asdf/bin/install нет списка выносимых имён")
    else:
        listed = set(spisok.group(1).split())
        if listed != scripts:
            beda.append(f"плагин выносит {sorted(listed)}, а пакет кладёт {sorted(scripts)}")

    # 7. asdf ждёт bin/ в корне хранилища: три файла на месте и исполняемые.
    for name in ("download", "install", "list-all"):
        shim = ROOT / "bin" / name
        real = ROOT / "packaging" / "asdf" / "bin" / name
        for path in (shim, real):
            if not path.is_file():
                beda.append(f"нет файла {path.relative_to(ROOT)}")
            elif not path.stat().st_mode & 0o111:
                beda.append(f"{path.relative_to(ROOT)} не исполняемый — asdf его не позовёт")
        if shim.is_file() and f"packaging/asdf/bin/{name}" not in shim.read_text(encoding="utf-8"):
            beda.append(f"bin/{name} не передаёт работу в packaging/asdf/bin/{name}")

    if beda:
        print("Имена в упаковке разошлись с пакетом:\n")
        for b in beda:
            print(f"  - {b}")
        print("\nИмена команд заводятся в pyproject.toml ([project.scripts]), "
              "подкоманды — в ouroboros/cli.py.")
        return 1

    print(f"Имена в упаковке сходятся с пакетом: команды {sorted(scripts)}, "
          f"подкоманд {len(known)}, версия {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
