# Уроборос

**Показывает, как код исполнялся на самом деле: какие функции звались, с какими
доводами, что вернули и что бросили.**

Инструмент дописывает в исходник запись о вызовах. Программа запускается как
обычно, и каждый вызов оставляет две строки JSON — на входе и на выходе. Дальше
эти строки читают глазами, фильтруют командой или отдают ИИ-агенту.

Языки: Python, JavaScript/TypeScript, C, C++, Elixir. Схема записи у всех одна.

---

## Четыре шага

### 1. Поставить

```sh
uv tool install git+https://github.com/digitable-lol/ouroboros
```

```
Installed 2 executables: ouroboros, ouroboros-mcp
```

Через Homebrew и asdf — [Установка](docs/install.md); там же подключение к
Claude Code и Cursor как сервера MCP.

### 2. Дописать запись о вызовах

Возьмём обычный файл `stats.py`:

```python
"""Средняя длительность запросов из журнала."""


def parse_line(line):
    name, _, ms = line.partition(" ")
    return name, int(ms)


def average(values):
    return sum(values) / len(values)


def report(lines):
    pairs = [parse_line(l) for l in lines]
    return average([ms for _, ms in pairs])


if __name__ == "__main__":
    print(report(["get 12", "put 30", "get 18"]))
    print(report([]))
```

```sh
ouroboros wrap-file stats.py
```

```json
{"ok": true, "path": "stats.py", "language": "python", "functions_wrapped": 3, "runtime_header": "ouroboros_runtime.py"}
```

Файл после этого — тот же самый, плюс три строки с `@_ouro_log` и одна строка
ввоза. Ни отступы, ни комментарии, ни строка описания модуля не тронуты:

```python
"""Средняя длительность запросов из журнала."""
from ouroboros_runtime import log as _ouro_log


@_ouro_log
def parse_line(line):
    name, _, ms = line.partition(" ")
    return name, int(ms)


@_ouro_log
def average(values):
    return sum(values) / len(values)


@_ouro_log
def report(lines):
    pairs = [parse_line(l) for l in lines]
    return average([ms for _, ms in pairs])


if __name__ == "__main__":
    print(report(["get 12", "put 30", "get 18"]))
    print(report([]))
```

Рядом появился `ouroboros_runtime.py` — тот самый помощник, который пишет
записи. Он ввозится только из стандартной библиотеки, ставить ничего не нужно.

### 3. Запустить как обычно

```sh
python3 stats.py
```

```
20.0
Traceback (most recent call last):
  File "/srv/tmp/ouro-work/demo/stats.py", line 24, in <module>
    print(report([]))
          ~~~~~~^^^^
  File "/srv/tmp/ouro-work/demo/ouroboros_runtime.py", line 144, in wrapper
    result = fn(*args, **kwargs)
  File "/srv/tmp/ouro-work/demo/stats.py", line 19, in report
    return average([ms for _, ms in pairs])
  File "/srv/tmp/ouro-work/demo/ouroboros_runtime.py", line 144, in wrapper
    result = fn(*args, **kwargs)
  File "/srv/tmp/ouro-work/demo/stats.py", line 13, in average
    return sum(values) / len(values)
           ~~~~~~~~~~~~^~~~~~~~~~~~~
ZeroDivisionError: division by zero
```

Возвращено то же и брошено то же, что и до обмазки. Рядом появился файл
`debug.info`.

> Так **не всегда**. Обмазка — это правка исходника, и она умеет менять поведение
> программы, а не только время её работы. Проверенные случаи — в
> [Границах](docs/limits.md#где-обмазка-меняет-поведение-программы). Прогоняйте
> проверки после обмазки, а не только до неё.

### 4. Прочитать записи

Что было брошено и с какими доводами:

```sh
ouroboros trace debug.info --outcome raised
```

```json
{
  "ok": true,
  "path": "debug.info",
  "calls_parsed": 7,
  "malformed": 0,
  "matched": 2,
  "returned": 2,
  "next_cursor": null,
  "in_flight": [],
  "in_flight_truncated": false,
  "records": [
    {
      "index": 5,
      "started": "2026-08-28T23:39:45.166",
      "call_id": "ef71eb89-6727-4cdf-a3b7-2ea54cff81e3",
      "name": "average",
      "args": "[]",
      "kwargs": "",
      "outcome_kind": "raised",
      "outcome": "ZeroDivisionError: division by zero",
      "duration": 3e-06,
      "cpu": null,
      "thread": "2864987.129949101195776"
    },
    {
      "index": 6,
      "started": "2026-08-28T23:39:45.166",
      "call_id": "cb4e33f4-d0b2-4099-bcf2-044e4a88c1fa",
      "name": "report",
      "args": "[]",
      "kwargs": "",
      "outcome_kind": "raised",
      "outcome": "ZeroDivisionError: division by zero",
      "duration": 6.8e-05,
      "cpu": null,
      "thread": "2864987.129949101195776"
    }
  ]
}
```

Здесь видно то, чего нет в отслеживании стека: `average` позвали **с пустым
списком**, и пришёл он туда из `report`, которого позвали тоже с пустым. Не
«где сломалось», а «с чем позвали».

Сводка по всем вызовам сразу:

```sh
ouroboros trace-stats debug.info
```

```json
  "by_function": [
    { "name": "parse_line", "count": 3, "result": 3, "raised": 0, "unknown": 0,
      "duration_seconds": { "min": 1e-06, "max": 2e-06, "mean": 2e-06, "total": 5e-06, "count": 3 } },
    { "name": "average",    "count": 2, "result": 1, "raised": 1, "unknown": 0,
      "duration_seconds": { "min": 2e-06, "max": 3e-06, "mean": 2e-06, "total": 5e-06, "count": 2 } },
    { "name": "report",     "count": 2, "result": 1, "raised": 1, "unknown": 0,
      "duration_seconds": { "min": 6.8e-05, "max": 0.000346, "mean": 0.000207, "total": 0.000414, "count": 2 } }
  ],
```

*(вывод сокращён: показаны только `by_function`; целиком он в
[Как это выглядит](docs/examples/trace-record.md))*

Всё, что выше, — вывод настоящих прогонов на обычной машине с Linux, Python
3.12.13. Пути в отслеживании стека — из того каталога, где прогон делался.

---

## Что лежит в `debug.info`

Только дописываемый файл, по одному объекту JSON в строке. **Две строки на
вызов**, связанные общим `id`:

```jsonl
{"p":"in","t":"2026-08-28T23:39:45.166","id":"e668ee33-…","ci":-1,"th":"2864987.129949101195776","fn":"average","a":"[12, 30, 18]","k":""}
{"p":"out","id":"e668ee33-…","fn":"average","r":"20.0","d":2e-06}
```

| ключ | где | что |
|---|---|---|
| `p` | обе | `in` — вошли в вызов, `out` — вышли (вернули или бросили) |
| `t` | `in` | время входа |
| `id` | обе | номер вызова; по нему строки и связываются |
| `ci` | `in` | номер ядра процессора; `-1`, если недоступен |
| `th` | `in` | поток: `<номер процесса>.<номер потока>` |
| `fn` | обе | имя функции (у C++ и Elixir — с именем класса или модуля) |
| `a` | `in` | позиционные доводы, снятые **до** выполнения тела |
| `k` | `in` | именованные доводы |
| `r` | `out` | что вернули |
| `x` | `out` | что бросили: `<Тип: сообщение>`; взаимоисключается с `r` |
| `d` | `out` | сколько шёл вызов, в секундах |

Отсюда самый дешёвый ответ на вопрос «где висит»: **строка входа без парной
строки выхода** — вызов вошёл и не вернулся. Такие вызовы `trace` и `trace-stats`
складывают в `in_flight` отдельно.

Полный разбор ключей и решений — [SPEC.md](SPEC.md).

---

## Ещё три вещи, которые пригодятся сразу

**Горячий файл целиком обмазывать не надо** — нужные записи утонут. Есть выбор
по именам:

```sh
ouroboros wrap-functions parser.c parse_header parse_body
```

**Отдельный черновик,** если портить рабочее дерево не хочется: `create` заводит
каталог с историей, `write` дописывает записи при сохранении, `execute`
запускает и сам подставляет путь к `debug.info`, `finish` переносит результат
в соседний каталог.

```sh
ouroboros create ~/работа/разбор
ouroboros write ~/работа/разбор stats.py < stats.py
ouroboros execute ~/работа/разбор -- python3 stats.py
```

**Код, который не разбирается, не сохраняется.** `write` в этом случае
отказывает, а не кладёт полуобмазанный файл.

Все 17 команд — `ouroboros --help`; разбор по шагам —
[Начало работы](docs/getting-started.md).

---

## Зачем это

**«Я не понимаю, что тут происходит.»** Чужой проект, легаси, вчерашняя ошибка,
которая не воспроизводится. Прочитать сорок тысяч строк дорого; прогнать и
посмотреть, какие функции живые, с чем их зовут и что они возвращают, — дёшево.

→ [Прологировать чужой код](docs/trace-existing-code.md)

**«ИИ пишет мне код и не понимает, как он исполняется.»** Модель, читающая
исходник, рассуждает о том, что **должно** произойти. По трассе видно, какие
ветви исполнялись, какие доводы встретились, какие вызовы бросили исключение.
Инструмент умеет работать сервером MCP — `ouroboros-mcp`, 17 инструментов.

→ [Чтобы ИИ понимал, как код исполняется](docs/with-ai.md)

## Чего инструмент не делает

> Трасса записывает, **как код себя вёл**, а не как он должен себя вести.

Программа с ошибкой даёт трассу, в которой ошибка выглядит нормой. Это не
оговорка внизу страницы, а устройство: [Границы](docs/limits.md).

Ещё одно, измеренное на стенде: на задачах, где ошибка целиком видна по
итоговому выводу, запись о вызовах не даёт ничего, и агент к ней не тянется —
0 из 3 прогонов. Отрицательный результат вместе с точной границей —
[bench/RESULTS.md](bench/RESULTS.md).

---

## Что в хранилище

| каталог | что там |
|---|---|
| [`ouroboros/`](ouroboros/) | сам пакет: командная строка, сервер MCP, черновик, языки |
| [`tests/`](tests/) | 167 проверок |
| [`bench/`](bench/) | стенд и его итоги |
| [`packaging/`](packaging/) | один файл-программа, образ, формула Homebrew, плагин asdf |
| [`docs/`](docs/) | страницы, они же опубликованы |
| [`design/`](design/) | исходное задание и разбор способа обмазки по языкам |
| [`SPEC.md`](SPEC.md) | договор о формате записи, общий для пяти языков |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | как устроено внутри и как добавить язык |

Собрать и проверить у себя:

```sh
git clone https://github.com/digitable-lol/ouroboros && cd ouroboros
uv sync
scripts/qa.sh    # ruff, mypy, pytest
```

## Страницы

| страница | о чём |
|---|---|
| [Установка](docs/install.md) | uv, Homebrew, asdf, из исходников, подключение сервера MCP |
| [Начало работы](docs/getting-started.md) | все команды, порядок работы, на чём спотыкаются |
| [Прологировать чужой код](docs/trace-existing-code.md) | по шагам: что делать, что видишь, как читать |
| [Чтобы ИИ понимал, как код исполняется](docs/with-ai.md) | сервер MCP и его 17 инструментов |
| [Обычная разработка](docs/in-development.md) | молчащая программа, регрессия, чего не стоит делать |
| [Языки](docs/languages.md) | пять языков, чем они отличаются в записи |
| [Границы](docs/limits.md) | чего инструмент не делает и почему это осознанно |
| [В чём смысл](docs/why.md) | зачем нужен и какую работу снимает |
| [Как это выглядит](docs/examples/index.md) | записи, сводка, настройка — целиком |

Те же страницы опубликованы: <https://digitable-lol.github.io/ouroboros/>.

## Состояние

Версия 0.2.1. Проверки: 167 из 167 (`ruff`, `mypy --strict`, `pytest`). Установка
проверена целиком, а не «по виду правильно»: `uv tool install`, `brew install`
вместе с `brew test`, `asdf plugin add` вместе с `asdf install` — и после каждой
поставленный инструмент обмазывал файл, запускал его и читал записи. Все пять
языков прогнаны по отдельности.

**Что известно сломанным.** Обмазка умеет менять поведение программы, а не только
время её работы: в JavaScript пропадает `"use strict"`, в C++ не собирается
`return {1, 2, 3}`. Два случая того же рода в Python (`from __future__` и строка
описания модуля) найдены и починены здесь же. Разбор и что с этим делать —
[Границы](docs/limits.md#где-обмазка-меняет-поведение-программы).

**Чего здесь нет.** Моста, превращающего записи в готовые примеры для
спецификации (`fts_extract_examples`, `fts-gate`). Он описан в навыке
[`skill/SKILL.md`](skill/SKILL.md), но это отдельная сборка поверх этого
инструмента, и сервер её операций не выкладывает.

## Лицензия

BSD 2-Clause — полный текст в [LICENSE](LICENSE).

Оговорка: файл навыка [`skill/SKILL.md`](skill/SKILL.md) несёт в своём заголовке
собственную пометку `license: Apache-2.0`. Она пришла вместе с навыком, и здесь
её не трогали.
