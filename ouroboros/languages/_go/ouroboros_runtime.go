// Ouroboros runtime logging helper (Go).
//
// The Go analogue of ouroboros_runtime.py. Standard library only, so it can be
// copied into any project and keep working after the draft is synced. It
// appends JSONL records to OUROBOROS_DEBUG_INFO in the exact format defined by
// SPEC.md — NOT via stdout.
//
// Two lines per call, paired by `id` (short keys keep the file compact):
//
//	{"p":"in","t":"<iso>","id":"<uuid>","ci":-1,"th":"<pid.goroutine>","fn":"<name>","a":"<args>","k":""}
//	{"p":"out","id":"<uuid>","fn":"<name>","r":"<repr>","d":<seconds>}
//	{"p":"out","id":"<uuid>","fn":"<name>","x":"<Type: message>","d":<seconds>}
//
// Instrumented code calls it by plain package-scope names, because this file is
// dropped into the SAME package as the file being wrapped — Go has no
// file-scoped import of a sibling file, so there is no import line to inject
// and nothing is ever spliced above a `//go:build` constraint:
//
//	__ouro_ctx := _ouroEnter("add", a, b)
//	defer func() {
//		if __ouro_p := recover(); __ouro_p != nil {
//			_ouroPanicked(__ouro_ctx, __ouro_p)
//			panic(__ouro_p)
//		}
//		_ouroReturned(__ouro_ctx, __ouro_r0)
//	}()
//
// The package clause below is rewritten to the wrapped file's own package when
// the helper is placed beside it (languages/go_lang.py). It says `main` here so
// that the shipped file is valid Go and can be gofmt'd and vetted in the tree.
package main

import (
	"crypto/rand"
	"fmt"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// _ouroMaxValue bounds one rendered value, in bytes — the same 200 the Python
// (reprlib), JavaScript and Elixir helpers use, so one huge argument costs the
// same in every language.
const _ouroMaxValue = 200

// _ouroMaxRecord is the hard ceiling on one record, in bytes including the
// newline. PIPE_BUF is 4096 on Linux, and SPEC.md §1 promises each record is
// written with a single append and stays under it — that promise is what lets
// several processes share one debug.info. The per-value ceiling alone does not
// deliver it: a call with thirty long arguments overruns PIPE_BUF, the kernel
// tears the line, and the parser then counts both halves as malformed and drops
// them — data lost without a warning.
const _ouroMaxRecord = 4096

// _ouroOmittedType marks an argument whose value cannot be read: an unnamed
// parameter (`func f(int)`) or the blank identifier (`func f(_ int)`). It is a
// distinct type rather than a sentinel value because the renderer has to
// recognise it with a type switch — comparing two `any` values with `==` panics
// when the dynamic type is uncomparable (a slice, a map, a function), and an
// argument of such a type is ordinary Go.
type _ouroOmittedType struct{}

// _ouroOmitted is what the wrapper passes in place of an unreadable parameter.
var _ouroOmitted = _ouroOmittedType{}

// _ouroCall is one in-flight call: the `in` record is written when it is
// created, the `out` record when the call returns or panics.
type _ouroCall struct {
	id   string
	name string
	t0   time.Time
}

func _ouroPath() string {
	if p := os.Getenv("OUROBOROS_DEBUG_INFO"); p != "" {
		return p
	}
	return "debug.info"
}

// _ouroNowISO is local ISO-8601 with millisecond precision and no timezone
// suffix, matching the Python helper's
// datetime.now().isoformat(timespec="milliseconds"). Whole seconds would leave
// two calls in the same second unorderable, which is what the millisecond field
// is there to prevent.
func _ouroNowISO() string {
	return time.Now().Format("2006-01-02T15:04:05.000")
}

// _ouroGoroutine is the goroutine id, parsed out of the first line of this
// goroutine's own stack dump ("goroutine 17 [running]:").
//
// This is the only way the standard library offers to learn it: the runtime
// deliberately exports no goroutine id, and no portable call gives an OS thread
// id either (syscall.Gettid is Linux-only). The cost is real and grows with the
// depth of the stack, because runtime.Stack walks the whole traceback whatever
// the buffer size — see docs/measurements.md for the measured curve. It is paid
// once per call, on the `in` record only, and BEFORE the duration clock starts,
// so it never leaks into the `d` field.
func _ouroGoroutine() uint64 {
	var buf [32]byte
	n := runtime.Stack(buf[:], false)
	s := buf[:n]
	const prefix = "goroutine "
	if len(s) < len(prefix) || string(s[:len(prefix)]) != prefix {
		return 0
	}
	var id uint64
	for _, c := range s[len(prefix):] {
		if c < '0' || c > '9' {
			break
		}
		id = id*10 + uint64(c-'0')
	}
	return id
}

// _ouroThread is the `th` token: "<pid>.<goroutine>". Both halves, like every
// other backend — the OS process alone cannot tell two goroutines apart, and
// the goroutine alone cannot tell two processes sharing one debug.info apart.
//
// Goroutine ids are reused after a goroutine exits, so the token identifies a
// concurrent writer at a moment in time, not for the life of the process.
func _ouroThread() string {
	return strconv.Itoa(os.Getpid()) + "." + strconv.FormatUint(_ouroGoroutine(), 10)
}

// _ouroUUID is a UUIDv4 drawn from crypto/rand.
//
// Drawn, not seeded from the clock: two processes started within the same
// second would then draw the same sequence, their records would pair across
// process boundaries, and the one thing two records per call exist for — an
// `in` with no `out` means a call that never returned — would stop being
// visible.
func _ouroUUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "00000000-0000-4000-8000-000000000000"
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	const hex = "0123456789abcdef"
	out := make([]byte, 0, 36)
	for i, v := range b {
		if i == 4 || i == 6 || i == 8 || i == 10 {
			out = append(out, '-')
		}
		out = append(out, hex[v>>4], hex[v&0x0f])
	}
	return string(out)
}

// _ouroUTF8Prefix is the longest prefix of s of at most n bytes that is still
// valid UTF-8. Cutting on a raw byte count can split a character in half, and
// half a character makes the whole JSON line undecodable — losing the record the
// ceiling exists to save.
func _ouroUTF8Prefix(s string, n int) string {
	if n <= 0 {
		return ""
	}
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}

func _ouroCap(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return _ouroUTF8Prefix(s, n) + "…"
}

// _ouroRepr renders one value the way Go itself renders it.
//
// Strings are quoted (Go's own %q), everything else goes through %v — which is
// the native dialect SPEC.md says each language keeps. Two things a reader
// should know: %v calls a value's own String()/Error() method if it has one,
// and %v follows a pointer to a struct one level. Both are Go's ordinary
// printing rules, but they mean the sink reads data the wrapped function might
// only have stored, so a value being mutated by another goroutine can show up
// as a data race under `go test -race` that the uninstrumented program did not
// have.
func _ouroRepr(v any) string {
	var s string
	switch t := v.(type) {
	case _ouroOmittedType:
		// An unnamed or blank parameter: the value exists but nothing in the
		// program can name it, so there is nothing honest to print.
		return "<...>"
	case nil:
		s = "<nil>"
	case string:
		s = strconv.Quote(t)
	default:
		s = fmt.Sprintf("%v", v)
	}
	return _ouroCap(s, _ouroMaxValue)
}

func _ouroJoin(vs []any) string {
	if len(vs) == 0 {
		return ""
	}
	parts := make([]string, len(vs))
	for i, v := range vs {
		parts[i] = _ouroRepr(v)
	}
	return strings.Join(parts, ", ")
}

// _ouroEscape emits s as the body of a JSON string literal. Hand-rolled, like
// the C and C++ sinks: encoding/json escapes <, > and & as \u00XX by default,
// which would make the same value read differently in a Go trace than in the
// other five.
func _ouroEscape(s string) string {
	var b strings.Builder
	b.Grow(len(s) + 2)
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '"':
			b.WriteString(`\"`)
		case c == '\\':
			b.WriteString(`\\`)
		case c == '\n':
			b.WriteString(`\n`)
		case c == '\r':
			b.WriteString(`\r`)
		case c == '\t':
			b.WriteString(`\t`)
		case c < 0x20:
			b.WriteString(fmt.Sprintf(`\u%04x`, c))
		default:
			b.WriteByte(c)
		}
	}
	return b.String()
}

// _ouroPart is one key/value pair of a record, in the order it is written.
// A part with raw set is emitted verbatim (a JSON number) and is never
// shortened; the others are emitted as JSON string literals.
type _ouroPart struct {
	key   string
	value string
	raw   bool
}

// _ouroShrinkable says whether a field may be shortened to fit the record
// ceiling. `fn`, `id` and `t` are what makes a torn record identifiable at all,
// so they are never touched.
func _ouroShrinkable(key string) bool {
	return key == "a" || key == "k" || key == "r" || key == "x"
}

func _ouroLine(parts []_ouroPart) string {
	var b strings.Builder
	b.WriteByte('{')
	for i, p := range parts {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteByte('"')
		b.WriteString(p.key)
		b.WriteString(`":`)
		if p.raw {
			b.WriteString(p.value)
			continue
		}
		b.WriteByte('"')
		b.WriteString(_ouroEscape(p.value))
		b.WriteByte('"')
	}
	b.WriteString("}\n")
	return b.String()
}

// _ouroBounded builds the line, halving its longest shortenable field until the
// whole record fits under the ceiling.
//
// Halving rather than a fixed cut because the overflow comes either from one
// enormous argument or from thirty ordinary ones, and the same rule has to
// handle both. Every field it touches ends in an ellipsis, so a reader can tell
// a shortened value from a complete one.
func _ouroBounded(parts []_ouroPart) string {
	line := _ouroLine(parts)
	if len(line) <= _ouroMaxRecord {
		return line
	}
	budget := _ouroMaxRecord - 64 // headroom for the ellipsis markers below
	trimmed := map[int]bool{}
	// Plain three-clause loop, not `for range 64`: ranging over an integer
	// needs Go 1.22, and this file is compiled inside whatever module it is
	// dropped into, whose go directive may be older.
	for attempt := 0; attempt < 64; attempt++ {
		longest := -1
		for i := range parts {
			if !_ouroShrinkable(parts[i].key) {
				continue
			}
			if longest == -1 || len(parts[i].value) > len(parts[longest].value) {
				longest = i
			}
		}
		if longest == -1 || parts[longest].value == "" {
			break
		}
		parts[longest].value = _ouroUTF8Prefix(parts[longest].value, len(parts[longest].value)/2)
		trimmed[longest] = true
		if len(_ouroLine(parts)) <= budget {
			break
		}
	}
	for i := range trimmed {
		parts[i].value += "…"
	}
	return _ouroLine(parts)
}

// _ouroWrite appends one whole record with a single write to a file opened
// O_APPEND, so concurrent processes and goroutines cannot interleave a line.
//
// A sink error is swallowed on purpose. Instrumentation that kills the program
// it observes has broken the one promise this tool makes; a debug.info that
// cannot be opened costs records, and that is the smaller loss.
func _ouroWrite(parts []_ouroPart) {
	line := _ouroBounded(parts)
	f, err := os.OpenFile(_ouroPath(), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	_, _ = f.WriteString(line)
	_ = f.Close()
}

// _ouroEnter writes the `in` record and starts the call's duration clock.
//
// `a` carries positional values only and `k` is empty: SPEC.md splits the two
// fields, and Go has no named arguments. Argument reprs are snapshotted here,
// before the body runs, so a function that mutates its inputs still records the
// values it was actually called with.
func _ouroEnter(name string, args ...any) *_ouroCall {
	c := &_ouroCall{id: _ouroUUID(), name: name}
	_ouroWrite([]_ouroPart{
		{key: "p", value: "in"},
		{key: "t", value: _ouroNowISO()},
		{key: "id", value: c.id},
		// `ci` is a CPU index, and no portable Go call returns one. -1 is the
		// contract's "unknown", parsed as null: a number that merely looks like
		// a CPU index would make a reader comparing `ci` across languages
		// compare two unrelated things.
		{key: "ci", value: "-1", raw: true},
		{key: "th", value: _ouroThread()},
		{key: "fn", value: name},
		{key: "a", value: _ouroJoin(args)},
		{key: "k", value: ""},
	})
	// The monotonic clock starts AFTER the entry write, so the sink's own write
	// cost is not charged to the call being measured. time.Now carries a
	// monotonic reading and time.Since uses it, so `d` is immune to clock jumps.
	c.t0 = time.Now()
	return c
}

func _ouroEmit(c *_ouroCall, key, outcome string) {
	seconds := time.Since(c.t0).Seconds()
	_ouroWrite([]_ouroPart{
		{key: "p", value: "out"},
		{key: "id", value: c.id},
		{key: "fn", value: c.name},
		{key: key, value: outcome},
		{key: "d", value: strconv.FormatFloat(seconds, 'f', 6, 64), raw: true},
	})
}

// _ouroReturned writes the `out` record of a call that returned normally.
// A function with no results records "(no value)", the same words the C and C++
// sinks use for a return they cannot render.
func _ouroReturned(c *_ouroCall, results ...any) {
	if len(results) == 0 {
		_ouroEmit(c, "r", "(no value)")
		return
	}
	_ouroEmit(c, "r", _ouroJoin(results))
}

// _ouroPanicked writes the `out` record of a call left by a panic. `x` is
// "<Type>: <message>" per SPEC.md: %T is the panic value's own concrete type
// (`string` for panic("bad"), `runtime.boundsError` for an index out of range)
// and %v is its message. A bare "(panic)" would name neither and could not be
// acted on.
func _ouroPanicked(c *_ouroCall, p any) {
	_ouroEmit(c, "x", _ouroCap(fmt.Sprintf("%T: %v", p, p), _ouroMaxValue))
}
