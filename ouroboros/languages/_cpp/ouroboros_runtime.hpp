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
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <exception>
#include <ostream>
#include <sstream>
#include <string>
#include <thread>
#include <type_traits>
#include <utility>

namespace _ouro {

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
		return os.str();
	} else {
		return "<...>";
	}
}

// Guard char* so a null pointer does not crash operator<<.
inline std::string repr(const char *s) { return s ? std::string(s) : std::string("(null)"); }

// Escape a string for embedding inside a JSON string literal (no surrounding
// quotes added). Handles the mandatory control chars and \ ".
inline std::string _jesc(const std::string &s) {
	std::string o;
	o.reserve(s.size() + 8);
	for (unsigned char c : s) {
		switch (c) {
		case '"': o += "\\\""; break;
		case '\\': o += "\\\\"; break;
		case '\n': o += "\\n"; break;
		case '\r': o += "\\r"; break;
		case '\t': o += "\\t"; break;
		default:
			if (c < 0x20) {
				char b[8];
				std::snprintf(b, sizeof b, "\\u%04x", c);
				o += b;
			} else {
				o += (char)c;
			}
		}
	}
	return o;
}

inline const char *_path() {
	const char *p = std::getenv("OUROBOROS_DEBUG_INFO");
	return p ? p : "debug.info";
}

// Thread token for `th`: a stable per-thread id (std::thread::id streamed). The
// concurrency signal — distinguishes threads sharing one debug.info.
inline std::string _thread_token() {
	std::ostringstream os;
	os << std::this_thread::get_id();
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
	if (!seeded) {
		std::srand((unsigned)std::time(nullptr));
		seeded = true;
	}
	unsigned char u[16];
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

struct Scope {
	std::string name, started, uuid, args, result, th;
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
			    started.c_str(), uuid.c_str(), _jesc(th).c_str(),
			    _jesc(name).c_str(), _jesc(args).c_str());
			std::fclose(f);
		}
		// start the duration clock AFTER logging entry, so the entry-write
		// overhead is not counted in the measured call duration.
		t0 = std::chrono::steady_clock::now();
	}

	~Scope() {
		double dur = std::chrono::duration<double>(
		    std::chrono::steady_clock::now() - t0).count();
		FILE *f = std::fopen(_path(), "a");
		if (f == nullptr)
			return;
		if (std::uncaught_exceptions() > entry_uncaught) {
			std::fprintf(f,
			    "{\"p\":\"out\",\"id\":\"%s\",\"fn\":\"%s\",\"x\":\"(exception)\",\"d\":%.6f}\n",
			    uuid.c_str(), _jesc(name).c_str(), dur);
		} else {
			std::fprintf(f,
			    "{\"p\":\"out\",\"id\":\"%s\",\"fn\":\"%s\",\"r\":\"%s\",\"d\":%.6f}\n",
			    uuid.c_str(), _jesc(name).c_str(), _jesc(result).c_str(), dur);
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
