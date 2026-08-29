/*
 * Ouroboros runtime logging helper (C) — userland AND kernel sinks.
 *
 * The instrumented code is identical in both contexts (it declares a
 * `struct _ouro_call __ouro __attribute__((cleanup(_ouro_emit)))` and calls
 * _ouro_enter/_ouro_set_result); only the sink below differs, selected by the
 * `_KERNEL` macro that NetBSD kernel builds define.
 *
 * Format (both sinks): two JSONL lines per call, paired by `id` (SPEC.md):
 *     {"p":"in","t":"<ts>","id":"<uuid>","fn":"<name>","a":"<args>","k":""}
 *     {"p":"out","id":"<uuid>","fn":"<name>","r":"<result>","d":<seconds>}
 * String fields are JSON-escaped by hand (_ouro_jesc) — no library JSON dep,
 * works freestanding in the kernel. C has no exceptions, so the C sink only
 * ever emits the `r` (result) variant; `x` (raised) is for the other backends.
 *
 *   * Userland (#else): formats with stdio and appends the two lines to the
 *     file named by OUROBOROS_DEBUG_INFO (default debug.info).
 *
 *   * Kernel (#ifdef _KERNEL): NO stdio. Formats with kernel snprintf(9) and
 *     emits via printf(9) to the kernel message buffer; time from
 *     getnanouptime(9); uuid from a lockless atomic seq (NOT cprng_strong32(9),
 *     which can block/assert under the pmap spinlocks we instrument).
 *     IMPORTANT, deliberately scoped: kernel use is for SELECTIVE, opt-in
 *     functions, NOT blanket instrumentation — a per-frame struct on the small
 *     kernel stack, printf volume, and reentrancy on the console/lock path make
 *     "instrument everything" infeasible. The struct is shrunk for the kernel
 *     and a recursion guard stops the sink re-entering itself. Stack/reentrancy/
 *     volume safety and the freestanding build are verified on the target kernel,
 *     not by the local userland shim (which only checks record formatting).
 */
#ifndef OUROBOROS_RUNTIME_H
#define OUROBOROS_RUNTIME_H

#ifdef _KERNEL
/* ===================== kernel sink ===================== */
#ifndef OURO_KERNEL_TEST
/* Light includes — all the MINIMAL probe needs. */
#include <sys/param.h>   /* MAXCPUS */
#include <sys/types.h>
#include <sys/systm.h>   /* printf, snprintf, vsnprintf, kpreempt_{disable,enable} */
#include <sys/atomic.h>  /* atomic_inc_uint_nv -- lockless seq */
#include <sys/cpu.h>     /* curcpu, cpu_index */
/*
 * Heavy includes for the FULL probe only. OUROBOROS_MINIMAL_ONLY (set when sweeping a
 * file with ONLY the stackless minimal probe) skips them — crucially <sys/proc.h>/<sys/lwp.h>,
 * which transitively pull <sys/mutex.h> and, included EARLY (above a file that does
 * `#define __MUTEX_PRIVATE` before its own <sys/mutex.h>), would suppress the private
 * MUTEX_OWNER/mtx_owner macros. Skipping them lets minimal mode instrument ANY kernel file.
 */
#ifndef OUROBOROS_MINIMAL_ONLY
#include <sys/stdarg.h>
#include <sys/time.h>    /* struct timespec */
#include <sys/timevar.h> /* getnanouptime */
#include <sys/lwp.h>     /* curlwp, struct lwp (l_lid, l_proc) -- thread identity */
#include <sys/proc.h>    /* struct proc (p_pid) -- thread identity */
#endif
#endif

#ifndef OUROBOROS_MINIMAL_ONLY
struct _ouro_call {
	const char *name;
	char started[24];
	char uuid[40];
	char args[128];
	char result[80];
	int ci;			/* CPU index at entry (cpu_index(curcpu())) */
	char th[24];		/* thread identity "<pid>.<lid>" (curlwp) */
	struct timespec _t0;	/* entry time (getnanouptime, monotonic) for duration */
};
#endif

/*
 * Escape `src` into `dst` (bounded by `n`, always NUL-terminated) for embedding
 * inside a JSON string literal — no surrounding quotes. Handles the mandatory
 * controls plus \ and "; UTF-8 bytes (>=0x80) pass through (valid JSON). Stops
 * early rather than overflow: a truncated arg is acceptable, a stack smash isn't.
 */
static inline void _ouro_jesc(const char *src, char *dst, size_t n)
{
	static const char hex[] = "0123456789abcdef";
	size_t i = 0;
	char e;

	if (n == 0)
		return;
	if (src == NULL)
		src = "";
	while (*src) {
		unsigned char c = (unsigned char)*src++;
		e = 0;
		switch (c) {
		case '"': e = '"'; break;
		case '\\': e = '\\'; break;
		case '\n': e = 'n'; break;
		case '\r': e = 'r'; break;
		case '\t': e = 't'; break;
		}
		if (e) {
			if (i + 2 >= n)
				break;
			dst[i++] = '\\';
			dst[i++] = e;
		} else if (c < 0x20) {
			if (i + 6 >= n)
				break;
			dst[i++] = '\\'; dst[i++] = 'u'; dst[i++] = '0'; dst[i++] = '0';
			dst[i++] = hex[(c >> 4) & 0xf];
			dst[i++] = hex[c & 0xf];
		} else {
			if (i + 1 >= n)
				break;
			dst[i++] = (char)c;
		}
	}
	dst[i] = '\0';
}

#ifndef OUROBOROS_MINIMAL_ONLY
static inline void _ouro_now(char *buf, size_t n)
{
	struct timespec ts;
	getnanouptime(&ts);
	/* %03ld is bounded with %1000 so the compiler can see ms < 1000 (3 digits)
	 * and -Werror=format-truncation doesn't fire on the caller's fixed buffer. */
	snprintf(buf, n, "uptime+%lld.%03ld",
	    (long long)ts.tv_sec, (long)(ts.tv_nsec / 1000000 % 1000));
}

static inline void _ouro_uuid(char *b)
{
	/*
	 * Lockless pseudo-uuid.  Deliberately NOT cprng_strong32(9): the strong
	 * CPRNG can take locks / assert, and we instrument paths that run under
	 * kpreempt_disable() and pmap spinlocks -- a blocking/asserting sink
	 * would panic in the logger instead of the code under study.  A per-call
	 * atomic counter (AMO on riscv, never blocks) mixed with the high-res
	 * uptime is unique enough to correlate trace records.
	 */
	static volatile unsigned int _ouro_seq;
	struct timespec ts;
	uint32_t r[4];
	unsigned char *u = (unsigned char *)r;
	unsigned int s = atomic_inc_uint_nv(&_ouro_seq);

	getnanouptime(&ts);
	r[0] = (uint32_t)ts.tv_sec;
	r[1] = (uint32_t)ts.tv_nsec;
	r[2] = s;
	r[3] = s * 2654435761u;		/* Knuth multiplicative scramble */
	u[6] = (u[6] & 0x0f) | 0x40;
	u[8] = (u[8] & 0x3f) | 0x80;
	snprintf(b, 40,
	    "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
	    u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7],
	    u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
}
#endif /* !OUROBOROS_MINIMAL_ONLY (full-probe helpers) */

#ifdef OUROBOROS_KERNEL_RING
/*
 * Ring-buffer sink (opt-in, -DOUROBOROS_KERNEL_RING). For HOT kernel paths the
 * printf-per-record sink floods the console and slows the kernel to a crawl
 * (every pmap_update over an emulated serial...). Instead, append each record to
 * a lockless fixed ring and emit NOTHING during the run; dump the whole ring ONCE
 * with _ouro_dump() — e.g. from ddb after a panic: `call _ouro_dump`. The ring
 * keeps the most-recent OURO_RING_SLOTS records (the trace leading to the event).
 *
 * Lockless: atomic_inc_uint_nv hands each call a unique sequence -> a private
 * slot (seq % SLOTS); two CPUs never touch the same slot. No printf, no locks, so
 * it is safe under kpreempt_disable()/pmap spinlocks and needs no re-entrancy guard.
 *
 * SHARED cross-TU ring: the ring DATA (_ouro_ring/_ouro_ring_seq) and _ouro_dump are
 * defined in exactly ONE owner TU — the file compiled with -DOUROBOROS_RING_OWNER —
 * and every other ring-mode TU sees them `extern`. So you can instrument MANY files at
 * once into ONE ring and dump them together with a single _ouro_dump (the whole-kernel
 * call tree in one boot). All TUs must agree on OURO_RING_SLOTS (the index modulo).
 */
#ifndef OURO_RING_SLOTS
#define OURO_RING_SLOTS 256
#endif
#ifndef OURO_RING_SLOTSZ
#define OURO_RING_SLOTSZ 384
#endif
#ifdef OUROBOROS_RING_OWNER
char _ouro_ring[OURO_RING_SLOTS][OURO_RING_SLOTSZ];
volatile unsigned int _ouro_ring_seq;
#else
extern char _ouro_ring[][OURO_RING_SLOTSZ];
extern volatile unsigned int _ouro_ring_seq;
#endif

static inline void _ouro_console(const char *buf)
{
	unsigned int s = atomic_inc_uint_nv(&_ouro_ring_seq) - 1u;
	char *slot = _ouro_ring[s % OURO_RING_SLOTS];
	size_t i = 0;
	while (buf[i] != '\0' && i + 1 < OURO_RING_SLOTSZ) {
		slot[i] = buf[i];
		i++;
	}
	slot[i] = '\0';
}

/* Print the ring oldest->newest (the JSONL records read_trace ingests). Defined in the
 * OWNER TU only (extern elsewhere); not static so ddb can `call _ouro_dump`. */
extern unsigned char _ouro_min_busy;	/* defined below; the dump gates self-instrumentation */
void _ouro_dump(void);
#ifdef OUROBOROS_RING_OWNER
void _ouro_dump(void)
{
	unsigned int seq = _ouro_ring_seq;
	unsigned int n = seq < OURO_RING_SLOTS ? seq : OURO_RING_SLOTS;
	unsigned int start = seq - n;
	unsigned int k;

	/* Gate out self-instrumentation for the duration of the dump: _ouro_dump prints
	 * thousands of records via printf, and printf is itself instrumented. Without this the
	 * probe fires for every printf mid-dump — churning the shadow stack and faulting before
	 * the dump finishes (observed: header printed, then reboot, no "ring dump end"). Setting
	 * the re-entrancy flag makes every probe call during the dump skip, so it completes. */
	_ouro_min_busy = 1;
	/* `seq` is the TOTAL records emitted this boot; `n` is how many the ring still holds.
	 * seq <= OURO_RING_SLOTS means the window reaches back to the very first call (capture
	 * is COMPLETE); seq > SLOTS means the ring wrapped and the oldest seq-SLOTS were lost. */
	printf("=== ouroboros ring dump: %u records (total seen %u) ===\n", n, seq);
	for (k = 0; k < n; k++)
		printf("%s", _ouro_ring[(start + k) % OURO_RING_SLOTS]);  /* record ends in \n */
	printf("=== ouroboros ring end ===\n");
	_ouro_min_busy = 0;
}
#endif /* OUROBOROS_RING_OWNER */

#else
/*
 * Re-entrancy guard: stops the sink (printf) from re-entering instrumented code
 * and recursing.  PER-CPU, not a single global, so two CPUs emitting at once never
 * block each other — neither drops its record (the flaw of the earlier global
 * flag).  Correctness rests on pinning to one CPU for the whole emit:
 *   - kpreempt_disable() makes cpu_index(curcpu()) stable (no migration between
 *     reading the index and clearing the slot — the migration bug the design notes
 *     warned about), and keeps the printf on that one CPU, so a re-entry via printf
 *     hits the SAME slot we set and is caught.
 *   - printf is callable with preemption disabled (interrupt/spinlock context).
 * `static` keeps the array per-translation-unit (each instrumented file has its
 * own per-CPU guard); sufficient, since only SAME-CPU recursion must be broken.
 */
static volatile unsigned char _ouro_in_emit[MAXCPUS];

static inline void _ouro_console(const char *buf)
{
	unsigned int id;

	kpreempt_disable();
	id = (unsigned int)cpu_index(curcpu());
	if (id >= MAXCPUS || _ouro_in_emit[id]) {
		kpreempt_enable();	/* re-entry on this CPU (or bad index) -> skip */
		return;
	}
	_ouro_in_emit[id] = 1;
	printf("%s", buf);
	_ouro_in_emit[id] = 0;
	kpreempt_enable();
}
#endif /* OUROBOROS_KERNEL_RING */

/*
 * Minimal probe (opt-in: wrap_functions(minimal=True)) for HOT / RECURSIVE /
 * deeply-locked kernel functions where the full `struct _ouro_call` + _ouro_enter
 * is unsafe: the per-frame struct (~300B) blows the small kernel stack in a tight
 * recursion (the segtab page-table walk), and getnanouptime/curlwp/args formatting
 * widen the fault surface. This records ONLY function identity + nesting DEPTH:
 *   - a per-CPU depth counter (fixed BSS, not stack);
 *   - one IN-record per call; the 1-byte cleanup marker just decrements the depth.
 * No id, no out-record, no timing — depth fully encodes the call tree (parent =
 * nearest-lower-dep predecessor on the same ci), and a dropped record loses one
 * edge but never invents one (the id-reconstruction phantom we hit in Q3 cannot
 * recur). Per-frame stack cost = 1 byte. `__noinline__` keeps _ouro_min_enter's
 * scratch buffer in ITS OWN frame (else inlining would re-import the per-frame cost
 * that defeats the stack fix); `__unused__` lets full-mode TUs that include this
 * header but never call it build clean under -Werror. Single-TU per ring (like
 * _ouro_dump). Assumes the kpreempt-disabled / -smp 1 contexts it targets, where
 * cpu_index is stable between enter and exit.
 */
/*
 * Single GLOBAL depth — NO curcpu()/per-CPU dependency. The probe must be callable from
 * the VERY FIRST C instruction of boot, before curcpu()/tp and the console are set up: a
 * tracer callback can run in ANY context (early boot, CPU bring-up, NMI), so it must not
 * deref per-CPU state that does not exist yet — that was the silent hang-before-banner when
 * the whole kernel was instrumented (cpu_index(curcpu()) on an unset tp). Correct under the
 * -smp 1 boot we use (one CPU; ci reported 0). SHARED across TUs in ring mode (owner-defined
 * / extern), so cross-file nesting keeps a continuous depth; static in non-ring mode.
 */
#if defined(OUROBOROS_KERNEL_RING) && !defined(OUROBOROS_RING_OWNER)
extern unsigned int _ouro_min_depth;
extern unsigned char _ouro_min_busy;
#elif defined(OUROBOROS_KERNEL_RING)
unsigned int _ouro_min_depth;
unsigned char _ouro_min_busy;
#else
static unsigned int _ouro_min_depth;
static unsigned char _ouro_min_busy;
#endif

/*
 * EDGE-DEDUP mode (OUROBOROS_EDGE_DEDUP, kernel-ring only). "Instrument EVERYTHING and dump
 * once" is impossible by raw records — a whole-kernel boot emits millions, and a ring big
 * enough to hold them all overruns the early bootstrap page-map (a 100MB ring hung right
 * after "NetBSD start"). But the CALL GRAPH is finite: each unique caller->callee edge need
 * be emitted ONCE. A shadow stack gives the caller (the probe only knows depth otherwise),
 * and a "seen" bitmap dedups edges, so the whole-boot graph fits a small, boot-safe ring and
 * one safe end-dump captures it COMPLETE (seq < SLOTS in the dump => no edge lost).
 * OURO_EDGE_BITS sizes the bitmap (1<<23 bits = 1MB -> ~8M edge slots, negligible collisions).
 */
#ifdef OUROBOROS_EDGE_DEDUP
#ifndef OURO_STACK_MAX
#define OURO_STACK_MAX 1024
#endif
#ifndef OURO_EDGE_BITS
#define OURO_EDGE_BITS (1u << 23)
#endif
#if defined(OUROBOROS_KERNEL_RING) && !defined(OUROBOROS_RING_OWNER)
extern const char *_ouro_stack[OURO_STACK_MAX];
extern unsigned int _ouro_sp;
extern unsigned char _ouro_edge_seen[OURO_EDGE_BITS / 8u];
#else
const char *_ouro_stack[OURO_STACK_MAX];
unsigned int _ouro_sp;
unsigned char _ouro_edge_seen[OURO_EDGE_BITS / 8u];
#endif

/* FNV-1a over caller\0callee -> bitmap index; returns 1 the FIRST time this edge is seen. */
static __attribute__((__unused__)) int
_ouro_edge_new(const char *ca, const char *ce)
{
	unsigned int h = 2166136261u;
	const char *p;
	unsigned int idx, byte, bit;

	for (p = ca; *p; p++) { h ^= (unsigned char)*p; h *= 16777619u; }
	h ^= 0u; h *= 16777619u;
	for (p = ce; *p; p++) { h ^= (unsigned char)*p; h *= 16777619u; }
	idx = h & (OURO_EDGE_BITS - 1u);
	byte = idx >> 3; bit = 1u << (idx & 7u);
	if (_ouro_edge_seen[byte] & bit)
		return 0;
	_ouro_edge_seen[byte] |= (unsigned char)bit;
	return 1;
}
#endif /* OUROBOROS_EDGE_DEDUP */

/*
 * RE-ENTRANCY GUARD (ftrace-style recursion protection): a function CALLED FROM the probe
 * itself — e.g. snprintf, which lives in subr_prf.c — is also instrumented, so without a
 * guard probe->snprintf->probe->... recurses forever (and you cannot instrument subr_prf.c
 * at all). _ouro_min_busy makes the inner re-entry skip, so the probe can instrument EVERY
 * file, the printf path included, with no `notrace` exclusions. Balanced: _ouro_min_enter
 * RETURNS 1 only when it actually recorded (incremented depth); the cleanup marker carries
 * that, so _ouro_min_exit decrements only for recorded calls (skipped re-entries don't drift
 * the depth). curcpu-free -> safe from the first C instruction; correct under -smp 1.
 */
static __attribute__((__noinline__, __unused__)) char
_ouro_min_enter(const char *name)
{
	char buf[160];
	char en[96];

	if (_ouro_min_busy)
		return (char)0;			/* re-entry (e.g. via the probe's own snprintf) -> skip */
	_ouro_min_busy = 1;
#ifdef OUROBOROS_EDGE_DEDUP
	{
		/* Read within bounds: _ouro_sp can DRIFT past OURO_STACK_MAX when a frame never
		 * returns (so its cleanup never pops — context-switch tails, longjmp). Clamp the
		 * read index so a drifted sp can't read off the end of the array (-> fault). */
		unsigned int top = _ouro_sp <= OURO_STACK_MAX ? _ouro_sp : OURO_STACK_MAX;
		const char *caller = top ? _ouro_stack[top - 1u] : "(root)";
		char eca[96];

		if (_ouro_sp < OURO_STACK_MAX)	/* shadow stack -> the caller the depth probe lacks */
			_ouro_stack[_ouro_sp] = name;
		_ouro_sp++;
		if (_ouro_edge_new(caller, name)) {	/* emit each unique edge ONCE */
			_ouro_jesc(caller, eca, sizeof eca);
			_ouro_jesc(name, en, sizeof en);
			snprintf(buf, sizeof buf,
			    "{\"p\":\"e\",\"ca\":\"%s\",\"fn\":\"%s\"}\n", eca, en);
			_ouro_console(buf);
		}
	}
#else
	{
		unsigned int d = _ouro_min_depth++;

		_ouro_jesc(name, en, sizeof en);
		snprintf(buf, sizeof buf,
		    "{\"p\":\"in\",\"dep\":%u,\"ci\":0,\"fn\":\"%s\"}\n", d, en);
		_ouro_console(buf);
	}
#endif
	_ouro_min_busy = 0;
	return (char)1;
}

static __attribute__((__unused__)) void
_ouro_min_exit(char *marker)
{
#ifdef OUROBOROS_EDGE_DEDUP
	if (*marker && _ouro_sp > 0u)		/* pop the shadow stack on every recorded entry */
		_ouro_sp--;
#else
	if (*marker && _ouro_min_depth > 0u)	/* decrement only for calls we actually recorded */
		_ouro_min_depth--;
#endif
}

#ifndef OUROBOROS_MINIMAL_ONLY
static inline void _ouro_emit_entry(struct _ouro_call *c)
{
	char buf[640];
	char en[96], ea[256];

	_ouro_jesc(c->name, en, sizeof en);
	_ouro_jesc(c->args, ea, sizeof ea);
	snprintf(buf, sizeof buf,
	    "{\"p\":\"in\",\"t\":\"%s\",\"id\":\"%s\",\"ci\":%d,\"th\":\"%s\","
	    "\"fn\":\"%s\",\"a\":\"%s\",\"k\":\"\"}\n",
	    c->started, c->uuid, c->ci, c->th, en, ea);
	_ouro_console(buf);
}

static inline void _ouro_enter(struct _ouro_call *c, const char *name,
    const char *fmt, ...)
{
	struct lwp *_l = curlwp;
	va_list ap;
	c->name = name;
	_ouro_now(c->started, sizeof c->started);
	_ouro_uuid(c->uuid);
	/* Thread/CPU identity — distinguishes concurrent callers (SMP races). cpu
	 * index is a per-CPU read; curlwp is always a valid lwp (idle lwp at worst). */
	c->ci = cpu_index(curcpu());
	snprintf(c->th, sizeof c->th, "%d.%d",
	    (int)_l->l_proc->p_pid, (int)_l->l_lid);
	va_start(ap, fmt);
	vsnprintf(c->args, sizeof c->args, fmt, ap);
	va_end(ap);
	snprintf(c->result, sizeof c->result, "(no value)");
	_ouro_emit_entry(c);		/* p:in: entered (may never complete) */
	/* Clock starts AFTER the entry record, so the sink's own formatting and
	 * console write are not charged to the call being measured (SPEC.md §4). */
	getnanouptime(&c->_t0);
}

static inline void _ouro_set_result(struct _ouro_call *c, const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	vsnprintf(c->result, sizeof c->result, fmt, ap);
	va_end(ap);
}

static inline void _ouro_emit(struct _ouro_call *c)
{
	char buf[640];
	char en[96], er[176];
	struct timespec now;
	long long ds;
	long dn;

	getnanouptime(&now);
	ds = (long long)(now.tv_sec - c->_t0.tv_sec);
	dn = now.tv_nsec - c->_t0.tv_nsec;
	if (dn < 0) { ds--; dn += 1000000000L; }
	_ouro_jesc(c->name, en, sizeof en);
	_ouro_jesc(c->result, er, sizeof er);
	snprintf(buf, sizeof buf,
	    "{\"p\":\"out\",\"id\":\"%s\",\"fn\":\"%s\",\"r\":\"%s\",\"d\":%lld.%06ld}\n",
	    c->uuid, en, er, ds, dn / 1000);
	_ouro_console(buf);
}
#endif /* !OUROBOROS_MINIMAL_ONLY (full kernel probe) */

#else
/* ===================== userland sink ===================== */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>	/* getpid -- thread/process identity */
#if defined(__linux__)
#include <sys/syscall.h>	/* SYS_gettid -- thread id, no libpthread needed */
#elif defined(__NetBSD__)
#include <lwp.h>		/* _lwp_self -- thread id, in libc */
#endif

struct _ouro_call {
	const char *name;
	char started[32];
	char uuid[40];
	char args[512];
	char result[256];
	int ci;			/* CPU index at entry (-1: not available in userland) */
	char th[40];		/* thread identity "<pid>.<tid>" */
	struct timespec _t0;	/* entry time (monotonic) for duration */
};

/*
 * Thread id for the `th` field. Deliberately NOT pthread_self(): on NetBSD that
 * would drag libpthread into a program that never asked for threads. The Linux
 * syscall and the NetBSD lwp call are both in libc.
 */
static inline unsigned long long _ouro_tid(void)
{
#if defined(__linux__)
	return (unsigned long long)syscall(SYS_gettid);
#elif defined(__NetBSD__)
	return (unsigned long long)_lwp_self();
#else
	return 0ULL;
#endif
}

/*
 * Monotonic clock for durations. `d` must measure elapsed time, and the wall
 * clock does not: it steps on an NTP correction or a DST change, which can
 * produce a negative duration or a call that appears to take an hour. Every
 * other backend already uses a monotonic source (Python perf_counter, JS
 * hrtime, C++ steady_clock, Elixir monotonic_time); this makes C agree.
 */
static inline void _ouro_mono(struct timespec *ts)
{
#if defined(CLOCK_MONOTONIC)
	clock_gettime(CLOCK_MONOTONIC, ts);
#else
	timespec_get(ts, TIME_UTC);
#endif
}

static inline const char *_ouro_path(void)
{
	const char *p = getenv("OUROBOROS_DEBUG_INFO");
	return p ? p : "debug.info";
}

/* See the kernel branch for the contract: bounded JSON-string escaping. */
static inline void _ouro_jesc(const char *src, char *dst, size_t n)
{
	static const char hex[] = "0123456789abcdef";
	size_t i = 0;
	char e;

	if (n == 0)
		return;
	if (src == NULL)
		src = "";
	while (*src) {
		unsigned char c = (unsigned char)*src++;
		e = 0;
		switch (c) {
		case '"': e = '"'; break;
		case '\\': e = '\\'; break;
		case '\n': e = 'n'; break;
		case '\r': e = 'r'; break;
		case '\t': e = 't'; break;
		}
		if (e) {
			if (i + 2 >= n)
				break;
			dst[i++] = '\\';
			dst[i++] = e;
		} else if (c < 0x20) {
			if (i + 6 >= n)
				break;
			dst[i++] = '\\'; dst[i++] = 'u'; dst[i++] = '0'; dst[i++] = '0';
			dst[i++] = hex[(c >> 4) & 0xf];
			dst[i++] = hex[c & 0xf];
		} else {
			if (i + 1 >= n)
				break;
			dst[i++] = (char)c;
		}
	}
	dst[i] = '\0';
}

static inline void _ouro_now(char *buf, size_t n)
{
	struct timespec ts;
	struct tm *tm;
	char base[24];

	/* timespec_get is ISO C11 — no POSIX feature macro needed. */
	timespec_get(&ts, TIME_UTC);
	tm = localtime(&ts.tv_sec);
	strftime(base, sizeof base, "%Y-%m-%dT%H:%M:%S", tm);
	/* %1000 bounds ms to 3 digits so -Werror=format-truncation can't fire. */
	snprintf(buf, n, "%s.%03ld", base, (long)(ts.tv_nsec / 1000000 % 1000));
}

static inline void _ouro_uuid(char *b)
{
	static int seeded = 0;
	unsigned char u[16];
	int i;

	if (!seeded) {
		srand((unsigned)time(NULL) ^ (unsigned)(uintptr_t)b);
		seeded = 1;
	}
	for (i = 0; i < 16; i++)
		u[i] = (unsigned char)(rand() & 0xff);
	u[6] = (u[6] & 0x0f) | 0x40; /* version 4 */
	u[8] = (u[8] & 0x3f) | 0x80; /* variant   */
	snprintf(b, 40,
	    "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
	    u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7],
	    u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]);
}

static inline void _ouro_emit_entry(struct _ouro_call *c)
{
	FILE *f = fopen(_ouro_path(), "a");
	char en[256], ea[1024];

	if (f == NULL)
		return;
	_ouro_jesc(c->name, en, sizeof en);
	_ouro_jesc(c->args, ea, sizeof ea);
	fprintf(f,
	    "{\"p\":\"in\",\"t\":\"%s\",\"id\":\"%s\",\"ci\":%d,\"th\":\"%s\","
	    "\"fn\":\"%s\",\"a\":\"%s\",\"k\":\"\"}\n",
	    c->started, c->uuid, c->ci, c->th, en, ea);
	fclose(f);
}

static inline void _ouro_enter(struct _ouro_call *c, const char *name,
    const char *fmt, ...)
{
	va_list ap;

	c->name = name;
	_ouro_now(c->started, sizeof c->started);
	_ouro_uuid(c->uuid);
	/* Thread/CPU identity. Userland has no portable stable CPU index (-1);
	 * the process id distinguishes forked tracers sharing one debug.info. */
	c->ci = -1;
	/* "<pid>.<tid>" -- both halves, like every other backend. The pid alone
	 * cannot tell two threads apart; the tid alone cannot tell two processes
	 * apart, and SPEC.md lets several processes append to one debug.info. */
	snprintf(c->th, sizeof c->th, "%ld.%llu", (long)getpid(), _ouro_tid());
	va_start(ap, fmt);
	vsnprintf(c->args, sizeof c->args, fmt, ap);
	va_end(ap);
	/* default outcome for void / fall-through / goto-out with no value */
	snprintf(c->result, sizeof c->result, "(no value)");
	_ouro_emit_entry(c);	/* p:in: entered */
	/* Start the duration clock AFTER the entry record is written, so the
	 * sink's own fopen/fprintf/fclose is not charged to the call being
	 * measured. It used to be started first, which inflated every C duration
	 * by the cost of one file append -- a median of 9 microseconds against
	 * C++'s 0, for the same function. SPEC.md §4 requires this order. */
	_ouro_mono(&c->_t0);
}

static inline void _ouro_set_result(struct _ouro_call *c, const char *fmt, ...)
{
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(c->result, sizeof c->result, fmt, ap);
	va_end(ap);
}

static inline void _ouro_emit(struct _ouro_call *c)
{
	char en[256], er[512];
	struct timespec now;
	FILE *f;
	long ds, dn;

	/* Stop the clock BEFORE opening the file: fopen is the sink's cost, not the
	 * call's. Reading it after the open charged every C duration with one file
	 * open -- a median of 10 microseconds against C++'s 0 for the same
	 * function, which made "calls slower than N" incomparable across languages. */
	_ouro_mono(&now);
	f = fopen(_ouro_path(), "a");
	if (f == NULL)
		return;
	ds = (long)(now.tv_sec - c->_t0.tv_sec);
	dn = now.tv_nsec - c->_t0.tv_nsec;
	if (dn < 0) { ds--; dn += 1000000000L; }
	_ouro_jesc(c->name, en, sizeof en);
	_ouro_jesc(c->result, er, sizeof er);
	fprintf(f,
	    "{\"p\":\"out\",\"id\":\"%s\",\"fn\":\"%s\",\"r\":\"%s\",\"d\":%ld.%06ld}\n",
	    c->uuid, en, er, ds, dn / 1000);
	fclose(f);
}

/*
 * Minimal probe (userland twin of the kernel one above) — same depth-stamped
 * IN-record schema so the codegen path is exercised and tests can run it on the
 * host. Single-threaded per process: one global depth, ci = -1.
 */
static unsigned int _ouro_min_depth_g;
static unsigned char _ouro_min_busy_g;

static __attribute__((__noinline__, __unused__)) char
_ouro_min_enter(const char *name)
{
	FILE *f;
	char en[256];
	unsigned int d;

	if (_ouro_min_busy_g)			/* re-entrancy guard (mirrors the kernel probe) */
		return (char)0;
	_ouro_min_busy_g = 1;
	d = _ouro_min_depth_g++;
	f = fopen(_ouro_path(), "a");
	if (f != NULL) {
		_ouro_jesc(name, en, sizeof en);
		fprintf(f, "{\"p\":\"in\",\"dep\":%u,\"ci\":%d,\"fn\":\"%s\"}\n", d, -1, en);
		fclose(f);
	}
	_ouro_min_busy_g = 0;
	return (char)1;
}

static __attribute__((__unused__)) void
_ouro_min_exit(char *marker)
{
	if (*marker && _ouro_min_depth_g > 0u)
		_ouro_min_depth_g--;
}

#endif /* _KERNEL */

#endif /* OUROBOROS_RUNTIME_H */
