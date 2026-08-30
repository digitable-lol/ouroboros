#!/usr/bin/env bash
# Снимает замеры по всем восьми языкам заново и печатает таблицы для docs/measurements.md.
#
# Ни одно число на странице замеров не вписано руками: всё, что там стоит, —
# вывод этой команды. Прогон занимает пару минут.
#
# Что делается: образцы из samples/ копируются в рабочий каталог ДВАЖДЫ — один
# раз как есть, второй раз обмазанными, — затем обе копии собираются (там, где
# нужна сборка) и запускаются по семь раз каждая. Стороннего в замере ничего
# нет: обмазывает та самая команда `ouroboros`, что лежит на PATH.
#
# Использование:
#     scripts/measure/run.sh [рабочий каталог]
#
# Рабочий каталог по умолчанию — ./.measure-work; он стирается в конце.
# Задайте свой, если хотите разобрать, что получилось.
#
# Что нужно на машине: python3, node, gcc, g++, elixir, go и команда `ouroboros`.
# Чего нет — тот язык пропускается с явным сообщением, остальные считаются.
set -euo pipefail

cd "$(dirname "$0")/../.."
SAMPLES="$PWD/scripts/measure/samples"
MEASURE="$PWD/scripts/measure/measure.py"
WORK="${1:-$PWD/.measure-work}"
REPEATS="${OUROBOROS_MEASURE_REPEATS:-7}"
CALLS=20000

if ! command -v ouroboros >/dev/null 2>&1; then
	echo "команды ouroboros нет на PATH — ставить смотрите docs/install.md" >&2
	exit 1
fi

rm -rf "$WORK"
mkdir -p "$WORK"
echo "== рабочий каталог: $WORK"
echo "== повторов на замер: $REPEATS, вызовов на прогон: $CALLS"
echo

RESULTS="$WORK/итог.jsonl"
: > "$RESULTS"

measure() {  # <имя> <файл записей или -> <рабочий каталог> -- <команда...>
	python3 "$MEASURE" "$@" | tee -a "$RESULTS"
}

# ---------------------------------------------------------------- Python ----
echo "== Python"
D="$WORK/python"; mkdir -p "$D"
cp "$SAMPLES/add.py" "$D/add.py"
cp "$SAMPLES/add.py" "$D/add_plain.py"
ouroboros wrap-file "$D/add.py"
measure "python-без" "$REPEATS" -                "$D" -- python3 "$D/add_plain.py" "$CALLS"
measure "python-с"   "$REPEATS" "$D/debug.info"  "$D" -- python3 "$D/add.py"       "$CALLS"
echo

# ------------------------------------------------------------ JavaScript ----
if command -v node >/dev/null 2>&1; then
	echo "== JavaScript"
	D="$WORK/javascript"; mkdir -p "$D"
	cp "$SAMPLES/package.json" "$D/"
	cp "$SAMPLES/add.js" "$D/add.js"
	cp "$SAMPLES/add.js" "$D/add_plain.js"
	ouroboros wrap-file "$D/add.js"
	measure "js-без" "$REPEATS" -               "$D" -- node "$D/add_plain.js" "$CALLS"
	measure "js-с"   "$REPEATS" "$D/debug.info" "$D" -- node "$D/add.js"       "$CALLS"
	echo
else
	echo "== JavaScript пропущен: нет node"; echo
fi

# ---------------------------------------------------------------------- C ----
if command -v gcc >/dev/null 2>&1; then
	echo "== C"
	D="$WORK/c"; mkdir -p "$D"
	cp "$SAMPLES/add.c" "$D/add.c"
	cp "$SAMPLES/add.c" "$D/add_plain.c"
	cp "$SAMPLES/add.c" "$D/minimal.c"
	cp "$SAMPLES/kinds.c" "$D/kinds.c"
	ouroboros wrap-file "$D/add.c"
	ouroboros wrap-file "$D/minimal.c" --minimal
	ouroboros wrap-file "$D/kinds.c"
	(cd "$D" && gcc -O2 -o add_plain add_plain.c \
	          && gcc -O2 -o add add.c \
	          && gcc -O2 -o minimal minimal.c \
	          && gcc -O2 -o kinds kinds.c)
	measure "c-без"     "$REPEATS" -                 "$D" -- "$D/add_plain" "$CALLS"
	measure "c-с"       "$REPEATS" "$D/debug.info"   "$D" -- "$D/add"       "$CALLS"
	measure "c-краткий" "$REPEATS" "$D/minimal.info" "$D" -- "$D/minimal"   "$CALLS"
	echo "-- что C записать не может (разные виды возврата):"
	(cd "$D" && OUROBOROS_DEBUG_INFO="$D/kinds.info" ./kinds >/dev/null && grep '"p":"out"' kinds.info)
	echo
else
	echo "== C пропущен: нет gcc"; echo
fi

# -------------------------------------------------------------------- C++ ----
if command -v g++ >/dev/null 2>&1; then
	echo "== C++"
	D="$WORK/cpp"; mkdir -p "$D"
	cp "$SAMPLES/add.cpp" "$D/add.cpp"
	cp "$SAMPLES/add.cpp" "$D/add_plain.cpp"
	cp "$SAMPLES/braced.cpp" "$D/braced.cpp"
	ouroboros wrap-file "$D/add.cpp"
	ouroboros wrap-file "$D/braced.cpp"
	(cd "$D" && g++ -O2 -std=c++17 -o add_plain add_plain.cpp \
	          && g++ -O2 -std=c++17 -o add add.cpp \
	          && g++ -std=c++17 -o braced braced.cpp)
	measure "cpp-без" "$REPEATS" -               "$D" -- "$D/add_plain" "$CALLS"
	measure "cpp-с"   "$REPEATS" "$D/debug.info" "$D" -- "$D/add"       "$CALLS"
	echo "-- возврат списком в скобках собрался и запустился:"
	(cd "$D" && OUROBOROS_DEBUG_INFO="$D/braced.info" ./braced && grep '"fn":"three"' braced.info | tail -1)
	echo
else
	echo "== C++ пропущен: нет g++"; echo
fi

# ----------------------------------------------------------------- Elixir ----
if command -v elixirc >/dev/null 2>&1; then
	echo "== Elixir"
	D="$WORK/elixir"; mkdir -p "$D/ebin" "$D/ebin_plain"
	cp "$SAMPLES/add.ex" "$D/add.ex"
	sed 's/defmodule Sample do/defmodule SamplePlain do/' "$SAMPLES/add.ex" > "$D/add_plain.ex"
	cp "$SAMPLES/run.exs" "$D/"
	cp ouroboros/languages/_elixir/ouroboros_trace.ex "$D/"
	ouroboros wrap-file "$D/add.ex"
	# Помощник собирается ПЕРВЫМ: `use Ouroboros.Trace` разворачивается во время сборки.
	(cd "$D" && elixirc -o ebin ouroboros_trace.ex add.ex >/dev/null \
	          && elixirc -o ebin_plain add_plain.ex >/dev/null)
	measure "elixir-без" "$REPEATS" -               "$D" -- elixir -pa "$D/ebin_plain" "$D/run.exs" SamplePlain "$CALLS"
	measure "elixir-с"   "$REPEATS" "$D/debug.info" "$D" -- elixir -pa "$D/ebin"       "$D/run.exs" Sample      "$CALLS"
	echo
else
	echo "== Elixir пропущен: нет elixirc"; echo
fi

# --------------------------------------------------------------------- Go ----
if command -v go >/dev/null 2>&1; then
	echo "== Go"
	D="$WORK/go"; mkdir -p "$D"
	cp "$SAMPLES/add.go" "$D/add.go"
	cp "$SAMPLES/add.go" "$D/add_plain.go"
	cp "$SAMPLES/goroutine_id.go" "$D/goroutine_id.go"
	ouroboros wrap-file "$D/add.go"
	# Помощник — файл того же пакета, а не ввоз: в сборке его надо назвать.
	(cd "$D" && go build -o add_plain add_plain.go \
	          && go build -o add add.go ouroboros_runtime.go \
	          && go build -o goroutine_id goroutine_id.go)
	measure "go-без" "$REPEATS" -               "$D" -- "$D/add_plain" "$CALLS"
	measure "go-с"   "$REPEATS" "$D/debug.info" "$D" -- "$D/add"       "$CALLS"
	echo "-- во что обходится узнать номер горутины (поле th):"
	(cd "$D" && ./goroutine_id)
	# Та же программа с тем же помощником, у которого номер горутины заменён
	# постоянным числом. Разница двух замеров и есть цена поля th — единственное,
	# чем Go отличается от прочих языков по расходам на запись.
	mkdir -p "$D/bez_th"
	python3 - "$D/ouroboros_runtime.go" "$D/bez_th/ouroboros_runtime.go" <<-'PY'
		import pathlib, sys
		src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
		start = src.index("func _ouroGoroutine() uint64 {")
		end = src.index("\n}\n", start) + 3
		stub = "func _ouroGoroutine() uint64 {\n\treturn 1\n}\n"
		out = (src[:start] + stub + src[end:]).replace('\t"runtime"\n', "")
		pathlib.Path(sys.argv[2]).write_text(out, encoding="utf-8")
	PY
	cp "$D/add.go" "$D/bez_th/add.go"
	(cd "$D/bez_th" && go build -o add add.go ouroboros_runtime.go)
	measure "go-без-th" "$REPEATS" "$D/bez_th/debug.info" "$D/bez_th" -- "$D/bez_th/add" "$CALLS"
	echo
else
	echo "== Go пропущен: нет go"; echo
fi

# ------------------------------------------------------------------- Java ----
if command -v javac >/dev/null 2>&1; then
	echo "== Java"
	D="$WORK/java"; mkdir -p "$D/plain" "$D/wrapped"
	cp "$SAMPLES/Add.java" "$D/plain/Add.java"
	cp "$SAMPLES/Add.java" "$D/wrapped/Add.java"
	ouroboros wrap-file "$D/wrapped/Add.java"
	cp ouroboros/languages/_java/OuroborosRuntime.java "$D/wrapped/"
	(cd "$D/plain" && javac -nowarn -d . Add.java)
	(cd "$D/wrapped" && javac -nowarn -d . Add.java OuroborosRuntime.java)
	measure "java-без" "$REPEATS" -               "$D/plain"   -- java -cp "$D/plain"   Add "$CALLS"
	measure "java-с"   "$REPEATS" "$D/debug.info" "$D/wrapped" -- java -cp "$D/wrapped" Add "$CALLS"
	echo
else
	echo "== Java пропущен: нет javac"; echo
fi

# --------------------------------------------------------------------- C# ----
if command -v dotnet >/dev/null 2>&1; then
	echo "== C#"
	D="$WORK/csharp"; mkdir -p "$D/plain" "$D/wrapped"
	FW="net$(dotnet --version | cut -d. -f1).0"
	for V in plain wrapped; do
		cp "$SAMPLES/Add.cs" "$D/$V/Add.cs"
		sed "s|net10\\.0|$FW|" "$SAMPLES/add.csproj" > "$D/$V/add.csproj"
	done
	ouroboros wrap-file "$D/wrapped/Add.cs"
	cp ouroboros/languages/_csharp/OuroborosRuntime.cs "$D/wrapped/"
	(cd "$D/plain"   && DOTNET_NOLOGO=1 dotnet build -c Release --nologo -v q >/dev/null)
	(cd "$D/wrapped" && DOTNET_NOLOGO=1 dotnet build -c Release --nologo -v q >/dev/null)
	measure "csharp-без" "$REPEATS" -               "$D/plain"   -- "$D/plain/bin/Release/$FW/add"   "$CALLS"
	measure "csharp-с"   "$REPEATS" "$D/debug.info" "$D/wrapped" -- "$D/wrapped/bin/Release/$FW/add" "$CALLS"
	echo
else
	echo "== C# пропущен: нет dotnet"; echo
fi

# ------------------------------------------------- глубина рекурсии Python ---
echo "== глубина рекурсии в Python"
D="$WORK/deep"; mkdir -p "$D"
cp "$SAMPLES/deep.py" "$D/deep.py"
cp "$SAMPLES/deep.py" "$D/deep_plain.py"
cp "$SAMPLES/deep_run.py" "$D/"
ouroboros wrap-file "$D/deep.py" >/dev/null
for L in 200 1000; do
	(cd "$D" && python3 deep_run.py deep_plain "$L" && python3 deep_run.py deep "$L")
done
echo

# ---------------------------------------------- строгий режим JavaScript -----
if command -v node >/dev/null 2>&1; then
	echo "== строгий режим JavaScript"
	D="$WORK/strict"; mkdir -p "$D"
	cp "$SAMPLES/strict.js" "$D/strict.js"
	cp "$SAMPLES/strict.js" "$D/strict_plain.js"
	ouroboros wrap-file "$D/strict.js" >/dev/null
	printf 'до обмазки:    %s\n' "$(cd "$D" && node strict_plain.js)"
	printf 'после обмазки: %s\n' "$(cd "$D" && OUROBOROS_DEBUG_INFO="$D/s.info" node strict.js)"
	echo
fi

# ------------------------------------------------------------- таблицы ------
echo "== сводные таблицы"
python3 "$(dirname "$0")/summary.py" "$RESULTS"

echo
echo "== готово. Сырые замеры: $RESULTS"
if [ "${1:-}" = "" ]; then
	echo "   (рабочий каталог по умолчанию не стирается — разберите и удалите сами)"
fi
