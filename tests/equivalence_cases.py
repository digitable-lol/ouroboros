"""Program corpus for the behaviour-equivalence suite (``test_equivalence.py``).

Each case is a whole, runnable program. The suite runs it twice — plain and
instrumented — and demands the same exit code, stdout and stderr. That is the
tool's central promise: *observing a program must not change it*. Anything the
wrap alters (a lost ``"use strict"``, a demoted module docstring, a dropped
copy elision, a file that stops compiling) shows up here as a failing case.

Cases are deliberately whole programs rather than unit assertions on the
transformer output: the only judge that cannot be fooled is the language
runtime itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    """One program to run plain and wrapped.

    ``filename`` decides the transformer's extension-driven behaviour (``.mjs``
    vs ``.cjs`` vs ``.js`` pick different module systems), so it is part of the
    case, not derived from it. ``executable`` runs the file directly via its
    ``#!`` line instead of handing it to an interpreter — the only way to catch
    a shebang that stopped being on line 1.
    """

    lang: str
    name: str
    source: str
    filename: str
    executable: bool = False

    @property
    def ident(self) -> str:
        return f"{self.lang}:{self.name}"


PYTHON: tuple[Case, ...] = tuple(
    Case("python", name, src, "prog.py")
    for name, src in {
        'docstring': r'''"""Module docstring."""
def f(x):
    return x * 2
print("doc:", repr(__doc__))
print("f:", f(3))
''',
        'future_import': r'''from __future__ import annotations
def f(x: int) -> int:
    return x + 1
print("f:", f(1))
''',
        'shebang': r'''#!/usr/bin/env python3
def f():
    return 7
print("f:", f())
''',
        'encoding_decl': r'''# -*- coding: utf-8 -*-
def f():
    return "\u0442\u0435\u043a\u0441\u0442"
print("f:", f())
''',
        'generator': r'''import inspect
def gen(n):
    for i in range(n):
        yield i
print("isgenfunc:", inspect.isgeneratorfunction(gen))
print("list:", list(gen(3)))
''',
        'async_fn': r'''import asyncio, inspect
async def work(x):
    await asyncio.sleep(0)
    return x * 3
print("iscoro:", inspect.iscoroutinefunction(work))
print("result:", asyncio.run(work(4)))
''',
        'recursion_limit': r'''import sys
sys.setrecursionlimit(200)
def deep(n):
    if n == 0:
        return 0
    return 1 + deep(n - 1)
try:
    print("deep:", deep(60))
except RecursionError as e:
    print("RecursionError")
''',
        'doctest': r'''def add(a, b):
    """Add.

    >>> add(1, 2)
    3
    """
    return a + b
import doctest, sys
r = doctest.testmod(sys.modules["__main__"], verbose=False)
print("doctest:", r.attempted, r.failed)
''',
        'introspection': r'''import inspect
class C:
    def m(self, a, b=2):
        return a + b
print("sig:", str(inspect.signature(C.m)))
print("name:", C.m.__name__)
print("src_ok:", "def m" in inspect.getsource(C.m))
print("m:", C().m(1))
''',
        'pickle_fn': r'''import pickle
def f(x):
    return x
print("pickled:", pickle.dumps(f) is not None)
''',
        'side_effect_free': r'''def add(a, b):
    return a + b
def mul(a, b):
    return a * b
print(add(2, 3), mul(4, 5))
''',
        'nested_closure': r'''def outer(n):
    def inner(m):
        return m + n
    return inner
print("r:", outer(10)(5))
''',
        'exception_flow': r'''def boom():
    raise ValueError("bad")
try:
    boom()
except ValueError as e:
    print("caught:", e)
''',
        'classmethod_static': r'''class C:
    @staticmethod
    def s(x):
        return x + 1
    @classmethod
    def c(cls, x):
        return x + 2
    @property
    def p(self):
        return 42
print(C.s(1), C.c(1), C().p)
''',
        'dataclass_slots': r'''from dataclasses import dataclass
@dataclass
class P:
    x: int
    def double(self):
        return self.x * 2
print(P(3), P(3).double())
''',
        'recursion_depth': r'''import sys
sys.setrecursionlimit(120)
def deep(n):
    if n == 0:
        return 0
    return 1 + deep(n - 1)
try:
    print("deep:", deep(50))
except RecursionError:
    print("RecursionError")
''',
        'func_defaults': r'''def f(a, b=10, *, c=20):
    return a + b + c
print("defaults:", f.__defaults__, f.__kwdefaults__)
print("f:", f(1))
''',
        'docstring_and_future': r'''"""Real module."""
from __future__ import annotations
def f() -> int:
    return 1
print(f())
''',
        'threading_order': r'''import threading
out = []
def work(i):
    out.append(i)
ts = [threading.Thread(target=work, args=(i,)) for i in range(5)]
for t in ts: t.start()
for t in ts: t.join()
print("len:", len(out))
''',
        'yield_from': r'''def inner():
    yield 1
    yield 2
def outer():
    yield from inner()
print(list(outer()))
''',
        'contextmanager': r'''from contextlib import contextmanager
@contextmanager
def cm():
    yield 5
with cm() as v:
    print("v:", v)
''',
    }.items()
) + (
    # Run through the `#!` line rather than `python prog.py`: the only way to
    # notice that the shebang stopped being the first line of the file.
    Case(
        "python",
        "shebang_executed",
        "#!/usr/bin/env python3\ndef f():\n    return 7\nprint('f:', f())\n",
        "prog.py",
        executable=True,
    ),
)

_JS_SOURCES: dict[str, str] = {
        'simple': r'''function add(a,b){ return a+b; }
console.log(add(2,3));
''',
        'use_strict_file': r'''"use strict";
function f(){
  try { undeclared = 1; return "assigned"; }
  catch(e){ return "threw:"+e.constructor.name; }
}
console.log(f());
''',
        'use_strict_fn': r'''function f(){
  "use strict";
  try { undeclared = 1; return "assigned"; }
  catch(e){ return "threw:"+e.constructor.name; }
}
console.log(f());
''',
        'generator': r'''function* g(n){ for(let i=0;i<n;i++) yield i; }
console.log([...g(3)]);
''',
        'fn_tostring': r'''function f(a,b){ return a+b; }
console.log(f.toString().length > 0, f.length, f.name);
console.log(f(1,2));
''',
        'try_finally_return': r'''function f(){ try { return 1; } finally { console.log("fin"); } }
console.log(f());
''',
        'closure_var_scope': r'''function f(){ var x = 1; if (true) { var x = 2; } return x; }
console.log(f());
''',
        'hoisted_fn': r'''function outer(){ return inner(); function inner(){ return 42; } }
console.log(outer());
''',
        'this_arguments': r'''const o = {
  v: 7,
  m: function(){ return this.v + arguments.length; },
};
console.log(o.m(1,2));
''',
        'async_await': r'''async function w(x){ await null; return x*2; }
w(5).then(v=>console.log("v:",v));
''',
        'throw_flow': r'''function boom(){ throw new Error("bad"); }
try { boom(); } catch(e){ console.log("caught:", e.message); }
''',
        'class_method': r'''class C { constructor(){ this.v=1; } m(x){ return x+this.v; } }
console.log(new C().m(4));
''',
        'shebang': r'''#!/usr/bin/env node
function f(){ return 3; }
console.log(f());
''',
        'recursion': r'''function fib(n){ if(n<2) return n; return fib(n-1)+fib(n-2); }
console.log(fib(12));
''',
        'labeled_break': r'''function f(){
  outer: for(let i=0;i<3;i++){
    for(let j=0;j<3;j++){ if(j==1) continue outer; }
  }
  return "ok";
}
console.log(f());
''',
        'getter_setter': r'''const o = {
  _v: 1,
  get v(){ return this._v; },
  set v(x){ this._v = x; },
};
o.v = 9; console.log(o.v);
''',
    'non_bmp_characters': '''// \U0001F600 above the function
function pick() {
  return "\U0001F600 tail";
}
console.log(pick());
''',
}

JAVASCRIPT: tuple[Case, ...] = tuple(
    Case("javascript", name, src, "prog.js") for name, src in _JS_SOURCES.items()
) + (
    Case("javascript", "cjs_script",
         "function f(){ return 5; }\nconsole.log(f());\n", "prog2.cjs"),
)

#: Same programs as ES modules. `.mjs` forces module semantics on node no matter
#: what the file's own syntax suggests, so a backend that picks `require` vs
#: `import` from the parse rather than from the extension breaks every one of
#: them ("require is not defined in ES module scope").
JAVASCRIPT_MJS: tuple[Case, ...] = tuple(
    Case("javascript", f"mjs_{name}", src, "prog.mjs") for name, src in _JS_SOURCES.items()
) + (
    Case("javascript", "mjs_esm_export",
         "export function f(){ return 5; }\nconsole.log(f());\n", "prog.mjs"),
)


C: tuple[Case, ...] = tuple(
    Case("c", name, src, "prog.c")
    for name, src in {
        'simple': r'''#include <stdio.h>
int add(int a,int b){ return a+b; }
int main(void){ printf("%d\n", add(2,3)); return 0; }
''',
        'side_effect_in_return': r'''#include <stdio.h>
int counter = 0;
int bump(void){ return ++counter; }
int main(void){ printf("%d %d %d\n", bump(), counter, bump()); return 0; }
''',
        'string_return': r'''#include <stdio.h>
const char *name(void){ return "hello"; }
int main(void){ printf("%s\n", name()); return 0; }
''',
        'null_string_arg': r'''#include <stdio.h>
int len(const char *s){ return s ? 1 : 0; }
int main(void){ printf("%d\n", len(0)); return 0; }
''',
        'recursion': r'''#include <stdio.h>
int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); }
int main(void){ printf("%d\n", fib(20)); return 0; }
''',
        'goto_cleanup': r'''#include <stdio.h>
int f(int n){ int r = 0; if(n<0) goto out; r = n*2; out: return r; }
int main(void){ printf("%d %d\n", f(3), f(-1)); return 0; }
''',
        'struct_return': r'''#include <stdio.h>
struct P { int x, y; };
struct P mk(int a){ struct P p = {a, a*2}; return p; }
int main(void){ struct P p = mk(3); printf("%d %d\n", p.x, p.y); return 0; }
''',
        'stdout_order': r'''#include <stdio.h>
void say(const char *s){ printf("%s\n", s); }
int main(void){ say("a"); say("b"); return 0; }
''',
        'varargs': r'''#include <stdio.h>
#include <stdarg.h>
int sum(int n, ...){
    va_list ap; va_start(ap,n);
    int t=0;
    for(int i=0;i<n;i++) t+=va_arg(ap,int);
    va_end(ap);
    return t;
}
int main(void){ printf("%d\n", sum(3,1,2,3)); return 0; }
''',
        'static_local': r'''#include <stdio.h>
int next(void){ static int c = 0; return ++c; }
int main(void){ printf("%d %d %d\n", next(), next(), next()); return 0; }
''',
        'main_wrapped_exit': r'''#include <stdio.h>
int main(void){ printf("x\n"); return 3; }
''',
        'const_char_arg_null': r'''#include <stdio.h>
#include <string.h>
size_t l(const char*s){ return strlen(s); }
int main(void){ printf("%zu\n", l("abcd")); return 0; }
''',
        'float_return': r'''#include <stdio.h>
double half(double x){ return x/2.0; }
int main(void){ printf("%.2f\n", half(5.0)); return 0; }
''',
        'stderr_vs_stdout': r'''#include <stdio.h>
int f(void){ fprintf(stderr, "E\n"); return 1; }
int main(void){ printf("O\n"); return f(); }
''',
    }.items()
)


CPP: tuple[Case, ...] = tuple(
    Case("cpp", name, src, "prog.cpp")
    for name, src in {
        'simple': r'''#include <iostream>
int add(int a,int b){ return a+b; }
int main(){ std::cout << add(2,3) << "\n"; }
''',
        'unique_ptr_return': r'''#include <iostream>
#include <memory>
std::unique_ptr<int> mk(int v){ return std::make_unique<int>(v); }
int main(){ std::cout << *mk(5) << "\n"; }
''',
        'reference_return': r'''#include <iostream>
int g = 7;
int &ref(){ return g; }
int main(){ ref() = 9; std::cout << g << "\n"; }
''',
        'exception': r'''#include <iostream>
#include <stdexcept>
int boom(){ throw std::runtime_error("bad"); }
int main(){
    try { boom(); }
    catch(const std::exception &e){ std::cout << "caught " << e.what() << "\n"; }
}
''',
        'copy_count': r'''#include <iostream>
struct T {
    int v;
    T(int x) : v(x) {}
    T(const T &o) : v(o.v) { std::cout << "copy\n"; }
    T(T &&o) : v(o.v) { std::cout << "move\n"; }
};
T mk(){ return T(1); }
int main(){ T t = mk(); std::cout << t.v << "\n"; }
''',
        'namespace_class': r'''#include <iostream>
namespace ns { struct C { int m(int x){ return x+1; } }; }
int main(){ ns::C c; std::cout << c.m(4) << "\n"; }
''',
        'vector_return': r'''#include <iostream>
#include <vector>
std::vector<int> mk(){ return {1,2,3}; }
int main(){ std::cout << mk().size() << "\n"; }
''',
        'recursion': r'''#include <iostream>
int fib(int n){ if(n<2) return n; return fib(n-1)+fib(n-2); }
int main(){ std::cout << fib(18) << "\n"; }
''',
        'static_local': r'''#include <iostream>
int next(){ static int c = 0; return ++c; }
int main(){ std::cout << next() << next() << next() << "\n"; }
''',
        'nonmovable_return': r'''#include <iostream>
#include <mutex>
struct NM { int v; NM(int x):v(x){} NM(const NM&)=delete; NM(NM&&)=delete; };
NM mk(){ return NM(3); }
int main(){ NM n = mk(); std::cout << n.v << "\n"; }
''',
        'braced_struct_return': r'''#include <iostream>
struct P { int x, y; };
P mk(){ return {1,2}; }
int main(){ P p = mk(); std::cout << p.x << p.y << "\n"; }
''',
        'initializer_list': r'''#include <iostream>
#include <map>
#include <string>
std::map<std::string,int> mk(){ return {{"a",1}}; }
int main(){ std::cout << mk().size() << "\n"; }
''',
        'noexcept_fn': r'''#include <iostream>
int f() noexcept { return 4; }
int main(){ std::cout << f() << "\n"; }
''',
        'constexpr_fn': r'''#include <iostream>
constexpr int sq(int x){ return x*x; }
int main(){ constexpr int v = sq(5); std::cout << v << "\n"; }
''',
        'dtor_order': r'''#include <iostream>
struct L {
    const char *n;
    L(const char *x) : n(x) { std::cout << "ctor " << n << "\n"; }
    ~L() { std::cout << "dtor " << n << "\n"; }
};
int f(){ L a("a"); return 1; }
int main(){ std::cout << f() << "\n"; }
''',
    }.items()
)


ELIXIR: tuple[Case, ...] = tuple(
    Case("elixir", name, src, "prog.exs")
    for name, src in {
        'simple': r'''defmodule M do
  def add(a, b), do: a + b
end
IO.puts(M.add(2, 3))
''',
        'guards_clauses': r'''defmodule M do
  def f(n) when n < 0, do: :neg
  def f(0), do: :zero
  def f(_), do: :pos
end
IO.inspect([M.f(-1), M.f(0), M.f(1)])
''',
        'defaults': r'''defmodule M do
  def f(a, b \\ 10), do: a + b
end
IO.puts(M.f(1))
''',
        'raise_flow': r'''defmodule M do
  def boom, do: raise("bad")
end
try do
  M.boom()
rescue
  e -> IO.puts("caught " <> Exception.message(e))
end
''',
        'private_fn': r'''defmodule M do
  def pub(x), do: priv(x) * 2
  defp priv(x), do: x + 1
end
IO.puts(M.pub(3))
''',
        'tail_recursion': r'''defmodule M do
  def loop(0, acc), do: acc
  def loop(n, acc), do: loop(n - 1, acc + n)
end
IO.puts(M.loop(200_000, 0))
''',
        'struct_module': r'''defmodule P do
  defstruct [:x, :y]
  def mk(a), do: %P{x: a, y: a * 2}
end
IO.inspect(P.mk(3))
''',
        'module_attr_doc': r'''defmodule M do
  @moduledoc "docs"
  def f, do: 1
end
IO.puts(M.f())
''',
        'behaviour_impl': r'''defmodule B do
  @callback go(integer) :: integer
end
defmodule M do
  @behaviour B
  @impl true
  def go(x), do: x + 1
end
IO.puts(M.go(1))
''',
        'pattern_binary': r'''defmodule M do
  def head(<<h::8, _rest::binary>>), do: h
end
IO.puts(M.head("ABC"))
''',
        'nested_module': r'''defmodule Outer do
  defmodule Inner do
    def f, do: 5
  end
  def g, do: Inner.f() + 1
end
IO.puts(Outer.g())
''',
    }.items()
)


GO: tuple[Case, ...] = tuple(
    Case("go", name, src, "prog.go")
    for name, src in {
        'simple': r"""package main

import "fmt"

func add(a, b int) int { return a + b }

func main() { fmt.Println(add(2, 3)) }
""",
        'multiple_results': r"""package main

import (
	"errors"
	"fmt"
)

func div(a, b int) (int, error) {
	if b == 0 {
		return 0, errors.New("divide by zero")
	}
	return a / b, nil
}

func main() {
	fmt.Println(div(6, 3))
	fmt.Println(div(1, 0))
}
""",
        'named_results_naked_return': r"""package main

import "fmt"

func split(n int) (half int, rest int) {
	half = n / 2
	rest = n - half
	return
}

func main() { fmt.Println(split(7)) }
""",
        'blank_result': r"""package main

import "fmt"

func f() (_ int, err error) { return 3, nil }

func main() { fmt.Println(f()) }
""",
        'deferred_result_change': r"""package main

import "fmt"

func f() (n int) {
	defer func() { n *= 10 }()
	return 4
}

func main() { fmt.Println(f()) }
""",
        'defer_order': r"""package main

import "fmt"

func f() int {
	defer fmt.Println("second")
	defer fmt.Println("first")
	return 1
}

func main() { fmt.Println(f()) }
""",
        'recover_in_caller': r"""package main

import "fmt"

func boom() int { panic("bad") }

func main() {
	defer func() { fmt.Println("caught:", recover()) }()
	fmt.Println(boom())
}
""",
        'recover_in_self': r"""package main

import "fmt"

func safe() (out string) {
	defer func() {
		if r := recover(); r != nil {
			out = fmt.Sprint("caught:", r)
		}
	}()
	panic("inner")
}

func main() { fmt.Println(safe()) }
""",
        'recursion': r"""package main

import "fmt"

func fib(n int) int {
	if n < 2 {
		return n
	}
	return fib(n-1) + fib(n-2)
}

func main() { fmt.Println(fib(18)) }
""",
        'variadic': r"""package main

import "fmt"

func sum(xs ...int) (total int) {
	for _, v := range xs {
		total += v
	}
	return
}

func main() { fmt.Println(sum(1, 2, 3), sum()) }
""",
        'method_receivers': r"""package main

import "fmt"

type Counter struct{ n int }

func (c *Counter) Bump(by int) int {
	c.n += by
	return c.n
}

func (c Counter) Value() int { return c.n }

func main() {
	c := &Counter{}
	c.Bump(2)
	c.Bump(3)
	fmt.Println(c.Value())
}
""",
        'interface_satisfaction': r"""package main

import "fmt"

type Shape interface{ Area() int }

type Square struct{ side int }

func (s Square) Area() int { return s.side * s.side }

func describe(s Shape) string { return fmt.Sprint("area=", s.Area()) }

func main() { fmt.Println(describe(Square{side: 4})) }
""",
        'goroutines_channel': r"""package main

import (
	"fmt"
	"sort"
	"sync"
)

func work(i int) int { return i * i }

func main() {
	var mu sync.Mutex
	var wg sync.WaitGroup
	got := []int{}
	for i := 1; i <= 4; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			v := work(n)
			mu.Lock()
			got = append(got, v)
			mu.Unlock()
		}(i)
	}
	wg.Wait()
	sort.Ints(got)
	fmt.Println(got)
}
""",
        'closure_literal': r"""package main

import "fmt"

func apply(f func(int) int, v int) int { return f(v) }

func main() {
	double := func(x int) int { return x * 2 }
	fmt.Println(apply(double, 21))
}
""",
        'os_exit_code': r"""package main

import (
	"fmt"
	"os"
)

func leave(code int) {
	fmt.Println("leaving")
	os.Exit(code)
}

func main() { leave(3) }
""",
        'stdout_and_stderr': r"""package main

import (
	"fmt"
	"os"
)

func warn(msg string) int {
	fmt.Fprintln(os.Stderr, "E:", msg)
	return 1
}

func main() {
	fmt.Println("O")
	os.Exit(warn("bad"))
}
""",
        'struct_return': r"""package main

import "fmt"

type Point struct{ X, Y int }

func mk(a int) Point { return Point{X: a, Y: a * 2} }

func main() {
	p := mk(3)
	fmt.Println(p.X, p.Y)
}
""",
        'build_tag_and_doc_comment': r"""//go:build !ouroboros_never

// Package main greets.
package main

import "fmt"

func greet() string { return "hi" }

func main() { fmt.Println(greet(), len("//go:build")) }
""",
        'generic_function': r"""package main

import "fmt"

func first[T any](xs []T, fallback T) T {
	if len(xs) == 0 {
		return fallback
	}
	return xs[0]
}

func main() {
	fmt.Println(first([]int{7, 8}, 0))
	fmt.Println(first([]string{}, "none"))
}
""",
        'init_function': r"""package main

import "fmt"

var seed int

func init() { seed = 5 }

func use() int { return seed * 2 }

func main() { fmt.Println(use()) }
""",
        'unicode_source': r"""package main

import "fmt"

// Считает длину строки в рунах.
func длина(строка string) int { return len([]rune(строка)) }

func main() { fmt.Println(длина("привет"), длина("hi")) }
""",
        'labeled_break': r"""package main

import "fmt"

func find(target int) int {
	i := 0
outer:
	for ; i < 10; i++ {
		for j := 0; j < 10; j++ {
			if i*j == target {
				break outer
			}
		}
	}
	return i
}

func main() { fmt.Println(find(12)) }
""",
        'shadowed_result_name': r"""package main

import "fmt"

func f(n int) (err error) {
	if n > 0 {
		err := fmt.Errorf("inner %d", n)
		_ = err
		return nil
	}
	return fmt.Errorf("outer")
}

func main() { fmt.Println(f(1), f(-1)) }
""",
    }.items()
)


#: The corpus the acceptance number is quoted against: every one of these
#: programs must behave identically wrapped and unwrapped. It was 79 programs
#: across five languages before Go was added.
CASES: tuple[Case, ...] = PYTHON + JAVASCRIPT + C + CPP + ELIXIR + GO

#: Kept separate from CASES so the historical "16 of 79" stays comparable. Same
#: programs, ES-module extension.
EXTRA_CASES: tuple[Case, ...] = JAVASCRIPT_MJS
