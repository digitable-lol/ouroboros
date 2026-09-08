---
title: What it looks like
---

**English** · [Русский](index.ru.md)

# What it looks like

Three pages of real output, nothing cut short:

- [A trace record](trace-record.md) — the whole `debug.info` file and every key
  explained.
- [Trace summary](extraction-report.md) — everything `trace-stats` and `trace`
  print, in full.
- [Configuration](configuration.md) — the MCP server, the environment variables,
  what has to be installed.

All of it is output from runs on an ordinary Linux machine (Python 3.12.13,
Node 26.7.0, gcc 15.2.0). Nothing was tidied up for looks: if the output says
`ci: -1` or an empty `cpus`, that is what it said.

A short end-to-end example — four steps from install to a record you have read —
is in the [repository README](https://github.com/digitable-lol/ouroboros#four-steps).
