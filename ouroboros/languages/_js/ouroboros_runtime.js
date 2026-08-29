// Ouroboros runtime logging helper (JavaScript / TypeScript).
//
// The JS analogue of ouroboros_runtime.py. Self-contained (Node built-ins only)
// so it can be copied into a draft project and keep working after sync. It
// appends JSONL records to OUROBOROS_DEBUG_INFO in the exact format defined by
// SPEC.md — NOT via stdout.
//
// Two lines per call, paired by `id` (short keys keep the file compact):
//   {"p":"in","t":"<iso>","id":"<uuid>","ci":<cpu>,"th":"<pid.threadId>","fn":"<name>","a":"<args>","k":"<kwargs>"}
//   {"p":"out","id":"<uuid>","fn":"<name>","r":"<repr>","d":<seconds>}
//   {"p":"out","id":"<uuid>","fn":"<name>","x":"<Err: msg>","d":<seconds>}
//
// Instrumented code calls (via a default import / require named `_ouro_rt`):
//   const ctx = _ouro_rt.enter(name, [arg1, arg2]);   // snapshots arg reprs now
//   ... try { return (__ouro_result = expr); }
//   catch (e) { _ouro_rt.exit_throw(ctx, e); throw e; }
//   finally { _ouro_rt.exit(ctx, __ouro_result); }

"use strict";

const fs = require("fs");
const crypto = require("crypto");
// threadId is 0 on the main thread, distinct per worker_threads Worker — the
// concurrency signal for the `th` field. Guarded for ancient Node without it.
let _threadId = 0;
try { _threadId = require("worker_threads").threadId; } catch (_e) { _threadId = 0; }

function debugInfoPath() {
  return process.env.OUROBOROS_DEBUG_INFO || "debug.info";
}

// Thread token for `th`: "<pid>.<threadId>" — distinguishes forked processes
// AND worker threads sharing one debug.info.
function threadToken() {
  return `${process.pid}.${_threadId}`;
}

// Local ISO-8601 with millisecond precision, no timezone suffix — matches the
// Python helper's datetime.now().isoformat(timespec="milliseconds").
function nowIso() {
  const d = new Date();
  const p = (n, l = 2) => String(n).padStart(l, "0");
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
    `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
  );
}

const MAX_REPR = 200;

function shortRepr(v) {
  let s;
  try {
    if (typeof v === "string") s = JSON.stringify(v);
    else if (v === undefined) s = "undefined";
    else if (typeof v === "function") s = `[Function ${v.name || "anonymous"}]`;
    else if (typeof v === "bigint") s = `${v}n`;
    else if (v instanceof Promise) s = "[Promise]";
    else s = JSON.stringify(v);
    if (s === undefined) s = String(v);
  } catch (_e) {
    s = String(v);
  }
  if (s.length > MAX_REPR) s = s.slice(0, MAX_REPR) + "…";
  return s;
}

// Hard ceiling on one record, in bytes including the newline. PIPE_BUF is 4096
// on Linux, and SPEC.md §1 promises each record is written with a single append
// and stays under it — that promise is what lets several processes share one
// debug.info. The per-value MAX_REPR alone does not deliver it: a call with 30
// arguments produced a 6314-byte line, the kernel tore it, and the parser
// silently counted both halves as malformed and dropped them.
const MAX_RECORD_BYTES = 4096;

// Fields that may be shortened to fit. `fn`/`id`/`t` are what makes a torn
// record identifiable at all, so they are never touched.
const SHRINKABLE = ["a", "k", "r", "x"];

function encode(obj) {
  return JSON.stringify(obj) + "\n";
}

// Halve the longest value field until the line fits — the overflow can come
// from one enormous argument or from thirty ordinary ones, and the same rule
// handles both. Every field touched ends in an ellipsis, so a reader can tell a
// shortened value from a complete one.
function bounded(obj) {
  let line = encode(obj);
  if (Buffer.byteLength(line, "utf8") <= MAX_RECORD_BYTES) return line;
  obj = Object.assign({}, obj);
  const trimmed = new Set();
  const budget = MAX_RECORD_BYTES - 64; // headroom for the ellipsis markers
  for (let i = 0; i < 64; i++) {
    let field = SHRINKABLE[0];
    for (const f of SHRINKABLE) {
      if ((obj[f] || "").length > (obj[field] || "").length) field = f;
    }
    const value = obj[field] || "";
    if (!value) break;
    obj[field] = value.slice(0, Math.floor(value.length / 2));
    trimmed.add(field);
    if (Buffer.byteLength(encode(obj), "utf8") <= budget) break;
  }
  for (const f of trimmed) obj[f] += "…";
  return encode(obj);
}

function writeln(obj) {
  // One JSON object per line; a single appendFileSync carries a whole record.
  fs.appendFileSync(debugInfoPath(), bounded(obj), "utf8");
}

function emitEntry(ctx) {
  // Entry (p:in) event: written when the call is entered. A ctx whose entry
  // event has no matching `out` never returned (hang/crash).
  // ci: -1 — Node has no portable stable CPU index (parsed as 'unknown').
  writeln({ p: "in", t: ctx.started, id: ctx.id, ci: -1, th: threadToken(),
            fn: ctx.name, a: ctx.argsRepr, k: ctx.kwargsRepr });
}

function enter(name, args) {
  const ctx = {
    started: nowIso(),
    id: crypto.randomUUID(),
    name: name,
    argsRepr: (args || []).map(shortRepr).join(", "),
    kwargsRepr: "", // JS has no kwargs; the field is kept for schema parity
  };
  emitEntry(ctx);
  // Monotonic start AFTER logging entry, so duration measures the call, not the
  // entry-write overhead. hrtime.bigint() is nanoseconds, immune to clock jumps.
  ctx.t0 = process.hrtime.bigint();
  return ctx;
}

function emit(ctx, outcome) {
  const dur = Number(process.hrtime.bigint() - ctx.t0) / 1e9;
  writeln(Object.assign({ p: "out", id: ctx.id, fn: ctx.name }, outcome,
                        { d: Number(dur.toFixed(6)) }));
}

function exit(ctx, result) {
  emit(ctx, { r: shortRepr(result) });
}

function exit_throw(ctx, err) {
  const name = err && err.constructor && err.constructor.name ? err.constructor.name : "Error";
  const msg = err && err.message != null ? err.message : String(err);
  emit(ctx, { x: `${name}: ${msg}` });
}

module.exports = { enter, exit, exit_throw, shortRepr };
