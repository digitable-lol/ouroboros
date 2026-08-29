---
title: Сводка по записям
---

# Сводка по записям

Что печатают `trace-stats` и `trace`, полностью и без сокращений. Всё ниже —
вывод настоящего прогона той же программы из
[README](https://github.com/digitable-lol/ouroboros#четыре-шага): три функции,
семь вызовов, два из них с исключением.

## `trace-stats` — сводка целиком

```sh
ouroboros trace-stats debug.info
```

```json
{
  "ok": true,
  "path": "debug.info",
  "calls_parsed": 7,
  "malformed": 0,
  "total_calls": 7,
  "in_flight": [],
  "by_function": [
    {
      "name": "parse_line",
      "count": 3,
      "result": 3,
      "raised": 0,
      "unknown": 0,
      "duration_seconds": {
        "min": 1e-06,
        "max": 2e-06,
        "mean": 2e-06,
        "total": 5e-06,
        "count": 3
      }
    },
    {
      "name": "average",
      "count": 2,
      "result": 1,
      "raised": 1,
      "unknown": 0,
      "duration_seconds": {
        "min": 2e-06,
        "max": 3e-06,
        "mean": 2e-06,
        "total": 5e-06,
        "count": 2
      }
    },
    {
      "name": "report",
      "count": 2,
      "result": 1,
      "raised": 1,
      "unknown": 0,
      "duration_seconds": {
        "min": 6.8e-05,
        "max": 0.000346,
        "mean": 0.000207,
        "total": 0.000414,
        "count": 2
      }
    }
  ],
  "by_thread": [
    {
      "thread": "2864987.129949101195776",
      "count": 7,
      "functions": 3,
      "cpus": []
    }
  ],
  "duration_seconds": {
    "min": 1e-06,
    "max": 0.000346,
    "mean": 6.1e-05,
    "total": 0.000424,
    "count": 7
  },
  "timespan": {
    "first": "2026-08-28T23:39:45.166",
    "last": "2026-08-28T23:39:45.166",
    "seconds": 0.0,
    "timestamps_parsed": 7,
    "timestamps_unparsed": 0
  },
  "note": "counts/durations are over completed calls; `duration_seconds` are REAL per-call durations (exit−entry) from each call's `d`. `by_thread` groups calls by the `th` token (CPUs each thread ran on); empty for traces with no thread field. `in_flight` = entered (`p:in`) but never completed. `timespan` is first→last entry time."
}
```

### Как это читать

**Сверху вниз, а не слева направо.** Первые четыре поля отвечают на вопрос
«стоит ли вообще смотреть дальше»:

| поле | что означает | когда тревожно |
|---|---|---|
| `calls_parsed` | сколько вызовов завершилось | **0** — обмазанный код не исполнялся |
| `malformed` | сколько строк не разобралось | больше 0 — картина неполная |
| `in_flight` | вошли и не вернулись | непусто — зависание, падение или жёсткий выход |
| `total_calls` | столько же, но после отбора | меньше `calls_parsed` — сработал отбор |

**`by_function` отсортирован по числу вызовов**, от частых к редким. В каждой
строке `count` разложен на три: `result` (вернули), `raised` (бросили) и
`unknown` (строка выхода есть, а чем кончилось — не написано; так бывает у
оборванной записи).

Здесь видно сразу: `average` звали дважды, и **один раз он бросил**. Это и есть
след ошибки.

**`duration_seconds` — настоящие длительности каждого вызова**, взятые из поля
`d` каждой записи, а не вычитание меток времени. Поэтому `min`/`max` осмысленны
даже когда все вызовы уложились в одну миллисекунду и `timespan.seconds` равен
нулю.

Отдельно: `report` в среднем идёт в сто раз дольше `parse_line` — но это потому,
что внутри `report` считаются все остальные вызовы. Длительность вложенная, а не
собственная.

**`by_thread`** — сколько вызовов сделал каждый поток и на каких ядрах он шёл.
Здесь поток один и `cpus` пуст, потому что прогон был на Python: там номер ядра
всегда «неизвестно» ([Языки](../languages.md#ядро-поток-и-часы)).

**`timespan`** — от первого входа до последнего. `timestamps_unparsed` больше
нуля значит, что часть меток времени не разобралась.

## `trace` — сами записи, с отбором

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

`matched` — сколько записей прошло отбор всего; `returned` — сколько попало на
эту страницу. Если они расходятся, есть `next_cursor`: передайте его обратно
доводом `--cursor`, и придёт следующая страница. `next_cursor` равен `null` —
значит записи кончились.

`cpu: null` — то же «неизвестно», что и `-1` в сырой записи; читалка приводит их
к одному виду.

