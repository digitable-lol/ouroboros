---
title: Установка
---

# Установка

Четыре способа поставить инструмент и один необязательный шаг после — подключить
его к ИИ-агенту как сервер MCP.

## Что должно стоять на машине

| нужно | когда |
|---|---|
| Python 3.12 или новее | всегда |
| `gcc` или `clang` | чтобы обмазать и собрать C |
| `g++` или `clang++` | чтобы обмазать и собрать C++ |
| Node | чтобы обмазать и запустить JavaScript и TypeScript |
| `elixir` | чтобы собрать и запустить Elixir |
| `clang-tidy`, `clangd` | только для команд `lint`, `symbols`, `refs`, `callers`, `describe` |

`libclang` (разбор C и C++) ставится вместе с пакетом — это его зависимость,
отдельно доставлять не надо. Разбор JavaScript и TypeScript тоже уложен внутрь:
`@babel/parser` лежит в самом пакете, `npm install` не нужен, нужен только сам
`node`.

Разбор C и C++ вынесен в отдельную маленькую программу на C. Она собирается один
раз на машине при первом обмазывании и кладётся в кэш пользователя, поэтому
компилятор для C и C++ нужен уже на обмазывании. Заголовки llvm при этом не
нужны: программа объявляет тот кусок libclang, которым пользуется, сама, а
верность этих объявлений сверяется отдельной проверкой. Если в поставке есть
готовая собранная программа, путь к ней задаётся переменной
`OUROBOROS_CLANG_EMITTER`, и тогда компилятор на обмазывании не нужен.

Отдельной службы, базы данных или внешнего ключа не требуется.

## Способ 1. uv

Самый короткий и не требующий выпуска:

```sh
uv tool install git+https://github.com/digitable-lol/ouroboros
```

```
Installed 2 executables: ouroboros, ouroboros-mcp
```

Проверить:

```sh
ouroboros languages
```

```json
{"languages": ["python", "javascript", "c", "cpp", "elixir"]}
```

Обновить и удалить:

```sh
uv tool upgrade ouroboros-logger
uv tool uninstall ouroboros-logger
```

Имя пакета — `ouroboros-logger`, имена команд — `ouroboros` и `ouroboros-mcp`.

## Способ 2. Homebrew

```sh
brew install digitable-lol/tap/ouroboros
```

Первая часть имени — `digitable-lol/tap` — отдельное хранилище формул
(в Homebrew такое называется tap; полное имя хранилища —
`digitable-lol/homebrew-tap`). `brew` подключит его сам, отдельная команда
`brew tap` не нужна.

Исходник формулы лежит здесь, в
[`packaging/homebrew/ouroboros.rb`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/homebrew/ouroboros.rb),
а выложенная копия — в самом tap:
[`digitable-lol/homebrew-tap`](https://github.com/digitable-lol/homebrew-tap),
файл `Formula/ouroboros.rb`. Правки вносятся в исходник, в tap выкладываются
копией при выпуске.

Формула ставит пакет в собственное окружение Python и выносит наружу обе
команды. Python 3.12 Homebrew доставит сам — отдельно ставить его не надо.

Проверено прогоном на машине, где ни Homebrew, ни хранилища формул до этого не
было: одна строка `brew install digitable-lol/tap/ouroboros` подключила tap
сама, поставила Python 3.12 и пакет, `brew test` прошёл, после чего поставленный
`ouroboros` обмазал файл, файл запустился и записи прочитались.

> **Если `brew` отказывает по правам доступа.** Установка может оборваться так:
>
> ```
> Cloning into '.../Taps/digitable-lol/homebrew-tap'...
> git@github.com: Permission denied (publickey).
> fatal: Could not read from remote repository.
> ```
>
> Хранилище формул тут ни при чём: оно открытое и читается без всякого ключа.
> Примета видна в самом отказе — `brew` просил адрес на `https://`, а ругань
> пришла про `git@github.com` и ключ. Значит, адрес подменили по дороге, и
> сделали это ваши собственные настройки git: правило `insteadOf`. Его часто
> заводят себе те, кто сам пишет в эти же хранилища. Посмотреть, есть ли оно:
>
> ```sh
> git config --get-regexp 'url\..*\.insteadof'
> ```
>
> ```
> url.git@github.com:digitable-lol/.insteadof https://github.com/digitable-lol/
> ```
>
> Лечится заменой `insteadOf` на `pushInsteadOf`: тогда подменяется только
> отправка, а чтение остаётся на `https` — и `brew` работает.
>
> ```sh
> git config --global --unset url."git@github.com:digitable-lol/".insteadOf
> git config --global url."git@github.com:digitable-lol/".pushInsteadOf https://github.com/digitable-lol/
> ```
>
> Удобство при этом не теряется: `git push` по-прежнему уходит на
> `git@github.com` по вашему ключу, меняется только чтение. Разово подсунуть
> `GIT_CONFIG_GLOBAL=/dev/null` не поможет — `brew` чистит окружение перед тем,
> как позвать git.

Обновление и удаление — как у любой формулы:

```sh
brew upgrade ouroboros
brew uninstall ouroboros
```

## Способ 3. asdf

`asdf` держит рядом несколько версий одного инструмента и переключает их по
файлу `.tool-versions` в проекте.

```sh
asdf plugin add ouroboros https://github.com/digitable-lol/ouroboros.git
asdf install ouroboros latest
asdf set ouroboros latest
```

Версии берутся прямо с тегов хранилища:

```sh
asdf list all ouroboros
```

```
0.2.0
0.2.1
0.3.0
0.3.1
```

В asdf старее 0.16 последняя строка пишется как `asdf local ouroboros latest`.

> **Про короткое имя.** `asdf plugin add ouroboros` без адреса ищет плагин в
> общем списке плагинов asdf; попасть туда — отдельный шаг, который ещё не
> сделан. До тех пор адрес хранилища указывают явно, как выше.

Наружу плагин выносит ровно две команды — `ouroboros` и `ouroboros-mcp`. Это
важно: вместе с пакетом в его окружение приезжают команды зависимостей —
`httpx`, `jsonschema`, `mcp`, `uvicorn`, `dotenv` и другие. Если вынести всё
подряд, `asdf` сделает обёртку на каждое имя, и они заслонят настоящие программы
с теми же именами на машине.

Проверено прогоном на asdf 0.20.0 при выпуске
<!--state:version-->0.3.1<!--/state-->: `plugin add` по адресу выше, `list all`
(печатает `0.2.0`, `0.2.1`, `0.3.0` и `0.3.1`), `install` — после чего наружу вынесены
ровно два имени, а поставленный `ouroboros` обмазал настоящий файл, файл
запустился и дал тот же вывод, что до обмазки, и записи прочитались: пять
вызовов, ноль испорченных строк, ноль незавершённых.

Устройство плагина —
[`packaging/asdf/README.md`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/asdf/README.md).

## Способ 4. Из исходников

```sh
git clone https://github.com/digitable-lol/ouroboros
cd ouroboros
uv sync
```

Дальше либо через `uv run`:

```sh
uv run ouroboros languages
uv run ouroboros-mcp        # сервер MCP через обычный ввод-вывод
```

либо проверить всё разом:

```sh
scripts/qa.sh
```

```
== ruff (lint, ouroboros + tests) ==
All checks passed!
== mypy (strict, ouroboros) ==
Success: no issues found in 24 source files
== pytest ==
........................................................................ [ 12%]
........................................................................ [ 24%]
   …
....                                                                     [100%]
580 passed in 237.23s (0:03:57)
== all gates passed ==
```

Восемь из этих проверок требуют `clang-tidy` и `clangd`. Если их нет на машине,
проверки пропускаются, и в конце будет `159 passed, 8 skipped` — это тоже
зелёный результат.

## Один файл-программа и образ

Кроме этого, пакет собирается в **один самодостаточный файл** (PyInstaller,
около 47 МБ, Python на машине не нужен) и в **образ со всеми языками сразу**.
Оба способа расписаны в
[`packaging/README.md`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/README.md):

```sh
uv run pyinstaller packaging/ouroboros.spec --noconfirm
./dist/ouroboros languages
```

```sh
docker build -t ouroboros-logger -f packaging/Dockerfile .
docker run --rm -i ouroboros-logger
```

## Подключить сервер MCP

Это нужно, только если вы хотите, чтобы инструментом пользовался ИИ-агент. Для
работы руками достаточно команды `ouroboros`.

После установки через uv, Homebrew или asdf команда `ouroboros-mcp` лежит на
`PATH`, и настройка короткая:

```json
{ "mcpServers": { "ouroboros": { "type": "stdio", "command": "ouroboros-mcp" } } }
```

Если работаете из исходников и ставить ничего не хотите:

```json
{ "mcpServers": { "ouroboros": {
    "type": "stdio", "command": "uv",
    "args": ["run", "--directory", "<путь к хранилищу>", "ouroboros-mcp"] } } }
```

Ровно такая настройка лежит в хранилище в файле
[`.mcp.json`](https://github.com/digitable-lol/ouroboros/blob/main/.mcp.json) —
Claude Code подхватывает её из корня проекта сам.

### Куда положить этот кусок

| клиент | где обычно лежит |
|---|---|
| Claude Code | `.mcp.json` в корне проекта — едет вместе с хранилищем и достаётся всей команде |
| Cursor | `.cursor/mcp.json` в проекте или `~/.cursor/mcp.json` для всех проектов сразу |
| любой другой клиент | свой файл настроек внешних инструментов; смотрите его документацию |

Строение блока одно и то же: имя сервера, `type`, `command`, при необходимости
`args`. Если ваш клиент ждёт настройку без внешнего `mcpServers` — оставьте
внутренность.

### Проверить, что подключилось

Спросите у агента список доступных ему инструментов. Должно появиться
**семнадцать** имён, начиная с `wrap_file`, `read_trace` и `trace_stats`. Полный
список и что каждый делает — [Чтобы ИИ понимал, как код исполняется](with-ai.md).

Проверить сервер без агента можно и руками — он разговаривает обычным JSON-RPC
через ввод-вывод:

```sh
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
 | ouroboros-mcp
```

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05", … ,"serverInfo":{"name":"ouroboros-logger","version":"1.29.1"}}}
```

> Число в `serverInfo` — это версия библиотеки MCP, а не версия уробороса. Так
> её проставляет сама библиотека, поэтому у вас будет та, которая приехала при
> установке; в прогоне выше это `1.29.1`. Версия самого инструмента лежит в
> `pyproject.toml` и сейчас равна `<!--state:version-->0.3.1<!--/state-->`.

## Переменные среды

| переменная | что делает |
|---|---|
| `OUROBOROS_DEBUG_INFO` | путь к файлу записей. Не задана — записи идут в `./debug.info` рядом с рабочим каталогом. `ouroboros execute` подставляет её сам |
| `OUROBOROS_MCP_TRANSPORT` | как сервер MCP разговаривает: `stdio` (по умолчанию), `sse` или `streamable-http`. Другое значение — сервер откажется запускаться и скажет, какие бывают |

Больше переменных у инструмента нет
([`ouroboros/runtime.py:62`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L62),
[`ouroboros/mcp/server.py:886`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/mcp/server.py#L886)).

## Дальше

- [Начало работы](getting-started.md) — все команды и порядок работы
- [Прологировать чужой код](trace-existing-code.md) — первый настоящий прогон по шагам
- [Границы](limits.md) — прочитать до того, как из трассы вырастут выводы
