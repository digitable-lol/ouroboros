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
| `go` | чтобы обмазать, собрать и запустить Go |
| JDK (`javac`, `java`) | чтобы обмазать и собрать Java |
| .NET SDK (`dotnet`) | чтобы обмазать и собрать C# |
| `clang-tidy`, `clangd` | только для команд `lint`, `symbols`, `refs`, `callers`, `describe` |

`libclang` (разбор C и C++) ставится вместе с пакетом — это его зависимость,
отдельно доставлять не надо. Разбор JavaScript и TypeScript тоже уложен внутрь:
`@babel/parser` лежит в самом пакете, `npm install` не нужен, нужен только сам
`node`. Разбору Go не нужно ничего стороннего: `go/parser` входит в саму поставку
языка, поэтому `go` нужен уже на обмазывании, а не только на сборке. Разборщики
Java и C# доставлять тоже не надо, и по той же причине: они уже лежат внутри
самих средств сборки. У Java это компилятор из JDK (`javax.tools` вместе с
`com.sun.source`), у C# — Roslyn внутри пакета .NET SDK; путь к нему выясняется
из `dotnet --list-sdks`, а не записан в дереве.

Разбор C и C++ вынесен в отдельную маленькую программу на C. Она собирается один
раз на машине при первом обмазывании и кладётся в кэш пользователя, поэтому
компилятор для C и C++ нужен уже на обмазывании. Заголовки llvm при этом не
нужны: программа объявляет тот кусок libclang, которым пользуется, сама, а
верность этих объявлений сверяется отдельной проверкой. Если в поставке есть
готовая собранная программа, путь к ней задаётся переменной
`OUROBOROS_CLANG_EMITTER`, и тогда компилятор на обмазывании не нужен.

Так же устроены разборщики Java и C#: это программы на Java и на C#, они
собираются один раз на машину и кладутся в кэш (0,73 с и 15 КБ у Java, 1,87 с и
32 МБ у C# — [Замеры](measurements.md#цена-разборщика-у-java-и-c)), поэтому JDK и
.NET SDK нужны уже на обмазывании. Готовые сборки задаются переменными
`OUROBOROS_JAVA_EMITTER` и `OUROBOROS_CSHARP_EMITTER`.

Отдельной службы, базы данных или внешнего ключа не требуется.

## Если установка обрывается на «Permission denied (publickey)»

Это самая частая беда, и хранилища в ней не виноваты: они открытые и читаются по
`https` без всякого ключа. Виновата настройка git на вашей машине.

Все четыре способа ниже в какой-то момент забирают файлы с GitHub через `git`.
Если в вашей настройке git есть правило `insteadOf`, которое переписывает адреса
`https://github.com/…` в `git@github.com:…`, то этот шаг молча уходит по ssh — и
падает, если ключа нет или он не заведён на GitHub. Правило часто заводят себе
те, кто сам пишет в эти же хранилища.

Примета видна прямо в отказе: просили адрес на `https`, а ругань пришла про
`git@github.com` и ключ. Посмотреть, есть ли правило:

```sh
git config --get-regexp 'url\..*\.insteadof'
```

```
url.git@github.com:digitable-lol/.insteadof https://github.com/digitable-lol/
```

Где какой способ обрывается:

| способ | на каком шаге | что видно |
|---|---|---|
| uv | `uv tool install git+https://…` | `git fetch … Permission denied (publickey)` |
| Homebrew | подключение хранилища формул: `Cloning into '…/Taps/digitable-lol/homebrew-tap'` | `Permission denied (publickey)` |
| asdf | `asdf plugin add` | `unable to clone plugin: и репозиторий существует.` — `asdf` показывает только последнюю строку чужой ругани, поэтому сообщение выглядит бессмысленным |
| из исходников | `git clone` | `Permission denied (publickey)` |

**Лечится заменой `insteadOf` на `pushInsteadOf`.** Тогда подменяется только
отправка, а чтение остаётся на `https`:

```sh
git config --global --unset url."git@github.com:digitable-lol/".insteadOf
git config --global url."git@github.com:digitable-lol/".pushInsteadOf https://github.com/digitable-lol/
```

Удобство не теряется: `git push` по-прежнему уходит на `git@github.com` по
вашему ключу, меняется только чтение.

Что важно знать заранее:

- **Разово подставить `GIT_CONFIG_GLOBAL=/dev/null` для `brew` не выйдет.**
  `brew` вычищает окружение перед тем, как позвать git, и оставляет только
  список известных ему имён; `GIT_CONFIG_GLOBAL` в этот список не входит
  (см. `Homebrew/bin/brew`, переменная `ENV_VAR_NAMES`). Для `asdf` и `uv` такая
  подстановка сработает, но правило всё равно проще поправить один раз.
- **Сам архив с исходниками правило не трогает.** И формула Homebrew, и плагин
  asdf забирают выпуск обычной загрузкой по `https`
  (`…/archive/refs/tags/v0.5.0.tar.gz`), а не через git. Ломается ровно один
  шаг — тот, где хранилище **клонируют**.
- Если править настройку git нельзя, остаётся клонировать нужное хранилище
  руками с прямого адреса `git@…` или подсунуть локальную копию. Обойти это
  изнутри формулы или плагина нельзя: клонирует не она и не он, а сам `brew`
  (`asdf`, `uv`).

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

Приставка `git+` значит, что `uv` берёт исходники через git. Если у вас есть
правило `insteadOf`, этот способ обрывается на «Permission denied (publickey)» —
см. [раздел выше](#если-установка-обрывается-на-permission-denied-publickey).

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

> **Про строчку «is not trusted».** Начиная с Homebrew 6.0 чужие хранилища
> формул требуют согласия. В выводе установки мелькнёт
>
> ```
> Warning: Skipping digitable-lol/tap because it is not trusted. Run `brew trust digitable-lol/tap` to trust it.
> ==> Trusted formula digitable-lol/tap/ouroboros
> ```
>
> Останавливаться не нужно: вы назвали формулу полным именем, и `brew` считает
> это согласием на неё — установка идёт дальше и согласие запоминается
> (`~/.homebrew/trust.json`). Дальше короткие имена работают: `brew info
> ouroboros`, `brew test ouroboros`, `brew upgrade ouroboros`. Чтобы
> предупреждение больше не мелькало и согласие распространялось на всё
> хранилище, скажите один раз:
>
> ```sh
> brew trust digitable-lol/tap
> ```

Проверено прогоном 30 августа 2026 года. Прогон был с нуля: прежняя установка
снята (`brew uninstall ouroboros` заодно убрал и Python 3.12), хранилище формул
отцеплено (`brew untap`), после чего одна строка `brew install
digitable-lol/tap/ouroboros` подключила хранилище сама, поставила восемнадцать
зависимостей вместе с Python 3.12 и сам пакет за минуту с небольшим. Дальше:
`brew test ouroboros` прошёл; поставленная команда напечатала все восемь языков;
она же обмазала настоящий файл на Python из четырёх функций (имена русскими
буквами, одна функция бросает исключение) — вывод до и после обмазки совпал
побайтово, в записях шесть вызовов, ноль испорченных строк, ноль незавершённых;
`ouroboros-mcp` ответил на `initialize` по JSON-RPC.

> **Если `brew` отказывает по правам доступа** и в ругани мелькает
> `git@github.com: Permission denied (publickey)` — это правило `insteadOf` в
> вашей настройке git, а не запрет на хранилище формул. Что делать —
> [раздел выше](#если-установка-обрывается-на-permission-denied-publickey).

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
0.4.0
0.5.0
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

Проверено прогоном 30 августа 2026 года на asdf 0.20.0, выпуск
<!--state:version-->0.5.0<!--/state-->, с пустого места: `plugin add` по адресу
выше, `list all ouroboros` (печатает шесть версий, от `0.2.0` до `0.5.0`),
`install ouroboros 0.5.0` (ставит из исходников за двенадцать секунд),
`set ouroboros 0.5.0`. После этого `asdf` завёл ровно две обёртки — `ouroboros`
и `ouroboros-mcp`, ни одной лишней, — и поставленная команда обмазала настоящий
файл на Python из четырёх функций: вывод до и после обмазки совпал побайтово, в
записях шесть вызовов, ноль испорченных строк, ноль незавершённых.
`ouroboros-mcp` через обёртку ответил на `initialize` по JSON-RPC.

> **Если `asdf plugin add` отвечает бессмыслицей** вроде
>
> ```
> unable to clone plugin: и репозиторий существует.
> ```
>
> — это правило `insteadOf` в вашей настройке git. `asdf` показывает только
> последнюю строку того, что сказал git, а сказал он «Permission denied
> (publickey) … Удостоверьтесь, что у вас есть необходимые права доступа и
> репозиторий существует». Что делать —
> [раздел выше](#если-установка-обрывается-на-permission-denied-publickey).
>
> Клонирует плагин сам `asdf`, поэтому защититься изнутри плагина нельзя. Свои
> собственные обращения к git плагин защищает: в `bin/list-all` стоит
> `GIT_CONFIG_GLOBAL=/dev/null`, и `asdf list all ouroboros` печатает версии даже
> при действующем правиле — это проверено отдельно.

Устройство плагина —
[`packaging/asdf/README.md`](https://github.com/digitable-lol/ouroboros/blob/main/packaging/asdf/README.md).

## Способ 4. Из исходников

```sh
git clone https://github.com/digitable-lol/ouroboros
cd ouroboros
uv sync
```

Правило `insteadOf` ломает и этот способ — на самом `git clone`; см.
[раздел выше](#если-установка-обрывается-на-permission-denied-publickey).

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
> `pyproject.toml` и сейчас равна `<!--state:version-->0.5.0<!--/state-->`.

## Переменные среды

| переменная | что делает |
|---|---|
| `OUROBOROS_DEBUG_INFO` | путь к файлу записей. Не задана — записи идут в `./debug.info` рядом с рабочим каталогом. `ouroboros execute` подставляет её сам |
| `OUROBOROS_MCP_TRANSPORT` | как сервер MCP разговаривает: `stdio` (по умолчанию), `sse` или `streamable-http`. Другое значение — сервер откажется запускаться и скажет, какие бывают |

Больше переменных у инструмента нет
([`ouroboros/runtime.py:62`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L62),
[`ouroboros/mcp/server.py:894`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/mcp/server.py#L894)).

## Дальше

- [Начало работы](getting-started.md) — все команды и порядок работы
- [Прологировать чужой код](trace-existing-code.md) — первый настоящий прогон по шагам
- [Границы](limits.md) — прочитать до того, как из трассы вырастут выводы
