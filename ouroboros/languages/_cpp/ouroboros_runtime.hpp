// Ouroboros runtime logging helper (C++).
//
// The C++ analogue of ouroboros_runtime.h. Header-only. Unlike C, C++ has
// operator<< and templates, so values are stringified generically:
//   * is_streamable<T> (SFINAE) -> stream with operator<<, else "<...>".
//   * capture(scope, expr) records the return value's repr and forwards it,
//     working for ANY returnable type (no default-construct / temp needed).
//   * the Scope destructor is the RAII "finally": it fires on normal return,
//     fall-through AND exception unwinding, emitting one JSONL `out` line.
//     std::uncaught_exceptions() distinguishes a thrown exit from a normal one.
//
// Format: two JSONL lines per call, paired by `id` (SPEC.md):
//   {"p":"in","t":"<iso>","id":"<uuid>","ci":-1,"th":"<thread>","fn":"<name>","a":"<args>","k":""}
//   {"p":"out","id":"<uuid>","fn":"<name>","r":"<repr>","d":<seconds>}
//   {"p":"out","id":"<uuid>","fn":"<name>","x":"(exception)","d":<seconds>}
//
// SCOPE: userland C++ (uses <cstdio>). Instrumented code #includes this header.
#pragma once

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <exception>
#include <ostream>
#include <sstream>
#include <string>
#include <thread>
#include <type_traits>
#include <typeinfo>
#include <unistd.h>
#include <utility>

#if defined(__GNUC__)
#include <cxxabi.h>	// demangle typeid().name() so `x` reads as the source type
#endif

namespace _ouro {

// Per-value ceiling, in bytes — the same 200 the Python (reprlib) and JS helpers
// use, so one huge argument costs the same everywhere.
inline constexpr std::size_t MAX_VALUE = 200;
// Per-record ceiling, in bytes. SPEC.md §1 promises each record is written with
// a single append and stays under PIPE_BUF (4096 on Linux) so processes sharing
// one debug.info cannot interleave a line. Per-value caps alone do not deliver
// it: a call with 30 arguments produced a 12310-byte line, the kernel tore it,
// and the parser silently counted both halves as malformed and dropped them.
// The field budgets below are chosen so the longest possible record fits.
inline constexpr std::size_t MAX_NAME = 200;
inline constexpr std::size_t MAX_ARGS = 3000;
inline constexpr std::size_t MAX_OUTCOME = 600;

// Truncate to at most `n` bytes without splitting a UTF-8 character in half —
// a half character makes the whole JSON line undecodable, i.e. loses the record
// this cap exists to save.
inline std::string _cap(const std::string &s, std::size_t n) {
	if (s.size() <= n)
		return s;
	std::size_t cut = n;
	while (cut > 0 && ((unsigned char)s[cut] & 0xc0) == 0x80)
		cut--;	// step back off continuation bytes to a character boundary
	return s.substr(0, cut) + "\xe2\x80\xa6";	// U+2026 HORIZONTAL ELLIPSIS
}

template <class T, class = void>
struct is_streamable : std::false_type {};
template <class T>
struct is_streamable<
    T, std::void_t<decltype(std::declval<std::ostream &>() << std::declval<const T &>())>>
    : std::true_type {};

template <class T>
std::string repr(const T &v) {
	if constexpr (is_streamable<T>::value) {
		std::ostringstream os;
		os << v;
		return _cap(os.str(), MAX_VALUE);
	} else {
		return "<...>";
	}
}

// A `const char *` prints as its ADDRESS, not its contents.
//
// Streaming it would build a std::string from it, which calls strlen, which
// assumes the pointer is a NUL-terminated string. The type does not say that:
// `put_one(const char *p)` called as `put_one(&c)` on a single char is ordinary,
// correct C++. Reading to the first zero then runs off the end of that object —
// instrumentation introducing undefined behaviour into a program that had none.
// Proven with AddressSanitizer: unwrapped clean, wrapped `stack-buffer-overflow
// ... READ of size 2` inside strlen, through this function.
//
// The readable rendering is a real loss and cannot be made safe: no type means
// "really a string". See FEATURE_REQUESTS.md.
inline std::string repr(const char *s) {
	if (!s) return std::string("(null)");
	std::ostringstream os;
	os << static_cast<const void *>(s);
	return os.str();
}

// Escape a string for embedding inside a JSON string literal (no surrounding
// quotes added), stopping at `max_bytes` of OUTPUT. Handles the mandatory
// control chars and \ ". It advances one whole UTF-8 character at a time and
// only appends a piece that fits, so the cap can never leave half an escape
// sequence or half a character behind — either would make the line undecodable
// and cost the record the cap exists to save.
inline std::string _jesc(const std::string &s, std::size_t max_bytes) {
	std::string o;
	o.reserve(s.size() < max_bytes ? s.size() + 8 : max_bytes);
	std::size_t i = 0;
	while (i < s.size()) {
		unsigned char c = (unsigned char)s[i];
		std::size_t width = 1;
		if (c >= 0xf0) width = 4;
		else if (c >= 0xe0) width = 3;
		else if (c >= 0xc0) width = 2;
		if (i + width > s.size())
			width = 1;	// truncated input: copy the stray byte
		std::string piece;
		if (width == 1) {
			switch (c) {
			case '"': piece = "\\\""; break;
			case '\\': piece = "\\\\"; break;
			case '\n': piece = "\\n"; break;
			case '\r': piece = "\\r"; break;
			case '\t': piece = "\\t"; break;
			default:
				if (c < 0x20) {
					char b[8];
					std::snprintf(b, sizeof b, "\\u%04x", c);
					piece = b;
				} else {
					piece = std::string(1, (char)c);
				}
			}
		} else {
			piece = s.substr(i, width);
		}
		if (o.size() + piece.size() > max_bytes)
			break;
		o += piece;
		i += width;
	}
	return o;
}

inline const char *_path() {
	const char *p = std::getenv("OUROBOROS_DEBUG_INFO");
	return p ? p : "debug.info";
}

// Thread token for `th`: "<pid>.<thread>" — the same two-part shape the Python
// and JS helpers emit. Both halves are needed: the pid alone cannot tell two
// threads apart, the thread id alone cannot tell two processes apart, and
// SPEC.md lets several processes append to one debug.info.
inline std::string _thread_token() {
	std::ostringstream os;
	os << (long)getpid() << "." << std::this_thread::get_id();
	return os.str();
}

inline std::string _now() {
	struct timespec ts;
	timespec_get(&ts, TIME_UTC);
	struct tm *tm = std::localtime(&ts.tv_sec);
	char base[24];
	std::strftime(base, sizeof base, "%Y-%m-%dT%H:%M:%S", tm);
	char out[40];
	std::snprintf(out, sizeof out, "%s.%03d", base, (int)(ts.tv_nsec / 1000000));
	return out;
}

inline std::string _uuid() {
	static bool seeded = false;
	unsigned char u[16];
	if (!seeded) {
		// Seeded from the clock ALONE, two processes started in the same second
		// drew the same sequence and produced the same call ids — measured: two
		// runs both emitted e7711583-517b-40f4-994c-7c40ce9d2d93. SPEC.md lets
		// several processes append to one debug.info, and duplicate ids there
		// pair the wrong `in` with the wrong `out`, which destroys exactly what
		// two records per call were for: a call that entered and never returned
		// stops being visible. Mixing in addresses (which ASLR varies per
		// process) and the pid separates them, the same way the C helper does.
		std::srand((unsigned)std::time(nullptr)
		    ^ (unsigned)(std::uintptr_t)&seeded
		    ^ (unsigned)(std::uintptr_t)u
		    ^ ((unsigned)getpid() << 16));
		seeded = true;
	}
	for (int i = 0; i < 16; i++)
		u[i] = (unsigned char)(std::rand() & 0xff);
	u[6] = (u[6] & 0x0f) | 0x40;
	u[8] = (u[8] & 0x3f) | 0x80;
	char b[40];
	std::snprintf(b, sizeof b,
	    "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
	    u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7], u[8], u[9], u[10], u[11],
	    u[12], u[13], u[14], u[15]);
	return b;
}

// Demangled name of a C++ type, so `x` reads "std::runtime_error: bad" rather
// than the ABI's "St13runtime_error".
inline const std::string &_unknown_exception() {
	static const std::string s = "unknown: (exception)";
	return s;
}

inline std::string _type_name(const std::type_info &ti) {
#if defined(__GNUC__)
	int status = 0;
	char *dem = abi::__cxa_demangle(ti.name(), nullptr, nullptr, &status);
	std::string out = (status == 0 && dem != nullptr) ? dem : ti.name();
	std::free(dem);
	return out;
#else
	return ti.name();
#endif
}

struct Scope {
	std::string name, started, uuid, args, result, th, exc;
	int entry_uncaught;
	std::chrono::steady_clock::time_point t0;	// monotonic, for duration

	Scope(const char *n, std::string a)
	    : name(n), started(_now()), uuid(_uuid()), args(std::move(a)),
	      result("(no value)"), th(_thread_token()),
	      entry_uncaught(std::uncaught_exceptions()) {
		// `p:in` entry event: written on entry, so a call that never returns
		// (throw-through/abort) still leaves a record. ci: -1 — no portable
		// stable CPU index in userland (parsed as 'unknown'); th identifies the thread.
		FILE *f = std::fopen(_path(), "a");
		if (f != nullptr) {
			std::fprintf(f,
			    "{\"p\":\"in\",\"t\":\"%s\",\"id\":\"%s\",\"ci\":-1,\"th\":\"%s\","
			    "\"fn\":\"%s\",\"a\":\"%s\",\"k\":\"\"}\n",
			    started.c_str(), uuid.c_str(), _jesc(th, MAX_NAME).c_str(),
			    _jesc(name, MAX_NAME).c_str(), _jesc(args, MAX_ARGS).c_str());
			std::fclose(f);
		}
		// start the duration clock AFTER logging entry, so the entry-write
		// overhead is not counted in the measured call duration.
		t0 = std::chrono::steady_clock::now();
	}

	// Called from the `catch (...)` clause the transformer splices around the
	// body. It has to be there and not in this destructor: during stack
	// unwinding std::current_exception() is null (measured — the exception is
	// being thrown, not yet handled), so a destructor-only design can never say
	// WHICH exception left the function. Inside a catch clause it is available,
	// and the clause rethrows unchanged, so control flow is untouched.
	void note() {
		std::exception_ptr ep = std::current_exception();
		if (!ep)
			return;
		try {
			std::rethrow_exception(ep);
		} catch (const std::exception &e) {
			exc = _type_name(typeid(e)) + ": " + _cap(e.what(), MAX_VALUE);
		} catch (...) {
			exc = "unknown: (exception not derived from std::exception)";
		}
	}

	~Scope() {
		double dur = std::chrono::duration<double>(
		    std::chrono::steady_clock::now() - t0).count();
		FILE *f = std::fopen(_path(), "a");
		if (f == nullptr)
			return;
		if (std::uncaught_exceptions() > entry_uncaught) {
			// `note` (called from the catch clause the transformer splices in)
			// is what knows the type and message; without it all this frame can
			// say is that SOMETHING was thrown.
			const std::string &x = exc.empty() ? _unknown_exception() : exc;
			std::fprintf(f,
			    "{\"p\":\"out\",\"id\":\"%s\",\"fn\":\"%s\",\"x\":\"%s\",\"d\":%.6f}\n",
			    uuid.c_str(), _jesc(name, MAX_NAME).c_str(),
			    _jesc(x, MAX_OUTCOME).c_str(), dur);
		} else {
			std::fprintf(f,
			    "{\"p\":\"out\",\"id\":\"%s\",\"fn\":\"%s\",\"r\":\"%s\",\"d\":%.6f}\n",
			    uuid.c_str(), _jesc(name, MAX_NAME).c_str(),
			    _jesc(result, MAX_OUTCOME).c_str(), dur);
		}
		std::fclose(f);
	}
};

// Record the return value's repr and forward it unchanged. Works for any
// returnable type (value, reference, move-only) — no temp of the return type.
template <class T>
T &&capture(Scope &s, T &&v) {
	s.result = repr(static_cast<const std::remove_reference_t<T> &>(v));
	return std::forward<T>(v);
}

} // namespace _ouro
