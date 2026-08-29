---
title: Настройка
---

# Настройка

Настраивать в уроборосе почти нечего: два места — подключение сервера MCP и две
переменные среды. Всё ниже проверено прогоном.

## Подключение сервера MCP

Нужно, только если инструментом будет пользоваться ИИ-агент. Для работы руками
хватает команды `ouroboros`.

После установки (uv, Homebrew, asdf) команда `ouroboros-mcp` лежит на `PATH`:

```json
{ "mcpServers": { "ouroboros": { "type": "stdio", "command": "ouroboros-mcp" } } }
```

Если работаете из клона хранилища и ставить ничего не хотите:

```json
{ "mcpServers": { "ouroboros": {
    "type": "stdio", "command": "uv",
    "args": ["run", "--directory", "<путь к хранилищу>", "ouroboros-mcp"] } } }
```

| поле | что значит |
|---|---|
| `mcpServers` | список внешних инструментов, которые агент вправе звать |
| `ouroboros` | имя, под которым сервер будет виден агенту |
| `"type": "stdio"` | разговор идёт через обычный ввод-вывод запущенного процесса |
| `"command"` | что запускать: сам `ouroboros-mcp` либо `uv` |
| `"args"` | нужны только для `uv`: где лежит хранилище и что в нём запустить |

Куда положить этот кусок — [Установка](../install.md#куда-положить-этот-кусок).

Ровно такая настройка лежит в хранилище в файле
[`.mcp.json`](https://github.com/digitable-lol/ouroboros/blob/main/.mcp.json) —
Claude Code подхватывает её из корня проекта сам.

### Проверить без агента

Сервер разговаривает обычным JSON-RPC через ввод-вывод, поэтому его можно
опросить руками:

```sh
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
 | ouroboros-mcp
```

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05", … ,"serverInfo":{"name":"ouroboros-logger","version":"1.27.2"}}}
```

> `1.27.2` — версия библиотеки MCP, а не уробороса. Так её проставляет сама
> библиотека; версия инструмента лежит в `pyproject.toml`.

Список средств, который сервер объявляет в ответ на `tools/list`, — семнадцать
имён:

```
wrap_code_snippet   wrap_file        wrap_functions
read_trace          trace_stats
create_project      write_file       read_file       list_files   execute   finish
lint_file           symbol_search    document_symbols
references          call_hierarchy   describe_symbol
```

Что каждое делает — [Чтобы ИИ понимал, как код исполняется](../with-ai.md).

## Переменные среды

Их две, обе читаются в исходнике, и других нет.

| переменная | что делает |
|---|---|
| `OUROBOROS_DEBUG_INFO` | путь к файлу записей. Не задана — записи идут в `./debug.info` в рабочем каталоге процесса. `ouroboros execute` подставляет её сам, указывая на `<черновик>/debug.info` |
| `OUROBOROS_MCP_TRANSPORT` | как сервер MCP разговаривает: `stdio` (по умолчанию), `sse` или `streamable-http`. Другое значение — сервер откажется запускаться и назовёт допустимые |

Где они читаются:
[`ouroboros/runtime.py:52`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/runtime.py#L52)
и
[`ouroboros/mcp/server.py:823`](https://github.com/digitable-lol/ouroboros/blob/main/ouroboros/mcp/server.py#L823).

Пример: положить записи не рядом с программой, а в отдельный файл.

```sh
OUROBOROS_DEBUG_INFO=/путь/разбор.jsonl python3 stats.py
```

## Что нужно поставить

| нужно | когда |
|---|---|
| Python 3.12 или новее | всегда |
| `gcc` или `clang` | собрать обмазанный код на C |
| `g++` или `clang++` | обмазать и собрать C++ |
| Node | обмазать и запустить JavaScript и TypeScript |
| `elixir` | собрать и запустить Elixir |
| `clang-tidy`, `clangd` | только для `lint`, `symbols`, `refs`, `callers`, `describe` |

`libclang` и `@babel/parser` уложены внутрь пакета: доставлять их отдельно не
надо. Отдельной службы, базы данных или внешнего ключа не требуется.

## Что настраивается в самой обмазке

Почти ничего — и это осознанно. Настройка обмазки — это выбор, **что** обмазывать,
и делается он доводами команд, а не файлом настроек:

| нужно | как |
|---|---|
| обмазать не весь файл | `wrap-functions <файл> <имя>…` |
| посмотреть результат, не трогая файл | `wrap-file <файл> --stdout` |
| облегчённая запись для горячих функций (только C) | `--minimal` |

Файла настроек у инструмента нет.
