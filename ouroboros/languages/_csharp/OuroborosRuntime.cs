// Runtime logging helper injected into instrumented C# code.
//
// The C# analogue of ouroboros_runtime.py and _java/OuroborosRuntime.java. It
// uses nothing outside the base class library, so it can be copied into a draft
// project and keep working after the draft is synced. It appends JSONL records
// to the file named by OUROBOROS_DEBUG_INFO in the exact format SPEC.md defines
// — never via stdout.
//
// Two lines per call, paired by id:
//   {"p":"in","t":"<iso>","id":"<uuid>","ci":-1,"th":"<pid.thread>","fn":"<name>","a":"<args>","k":""}
//   {"p":"out","id":"<uuid>","fn":"<name>","r":"<repr>","d":<seconds>}
//   {"p":"out","id":"<uuid>","fn":"<name>","x":"<Type: message>","d":<seconds>}
//
// Instrumented code calls it by its full name, so no `using` is ever spliced
// into the observed file:
//   var c = Ouroboros.OuroborosRuntime.Enter("Cls.M", new object[]{a, b});
//   try { return Ouroboros.OuroborosRuntime.Ret<int>(c, expr); }
//   catch (System.Exception e) { Ouroboros.OuroborosRuntime.ExitThrow(c, e); throw; }
//   finally { Ouroboros.OuroborosRuntime.ExitPending(c); }

// The helper is dropped into whatever project is being observed, and that
// project's nullable setting is none of its business: without this the same
// file compiles clean under <Nullable>disable</Nullable> and raises seven
// CS8600/CS8604 warnings under <Nullable>enable</Nullable>, which a build with
// warnings-as-errors turns into a broken build caused purely by observing.
#nullable disable

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;

namespace Ouroboros
{
    public static class OuroborosRuntime
    {
        /// <summary>Cap on one rendered value, matching the other backends' short reprs.</summary>
        private const int MaxRepr = 200;

        /// <summary>
        /// Hard ceiling on one record, in bytes including the newline. PIPE_BUF is
        /// 4096 on Linux and SPEC.md section 1 promises each record is written with
        /// a single append and stays under it — that promise is what lets several
        /// processes share one debug.info. The per-value cap alone does not deliver
        /// it: a call with thirty long arguments overruns it, the kernel tears the
        /// line, and the parser counts both halves as malformed and drops them.
        /// </summary>
        private const int MaxRecordBytes = 4096;

        /// <summary>
        /// Fields that may be shortened to fit. fn/id/t are what makes a torn
        /// record identifiable at all, so they are never touched.
        /// </summary>
        private static readonly string[] Shrinkable = { "a", "k", "r", "x" };

        private const string Ellipsis = "…";

        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);

        private static readonly Dictionary<string, FileStream> Sinks =
            new Dictionary<string, FileStream>();

        private static readonly object WriteLock = new object();

        /// <summary>One in-flight call: the `in` record is written, the `out` is not.</summary>
        public sealed class Ctx
        {
            internal readonly string Id;
            internal readonly string Name;
            internal long Started;
            internal bool Done;

            /// <summary>Repr of the value the body returned, taken the moment it returned.</summary>
            internal string Pending = "(no value)";

            internal Ctx(string id, string name)
            {
                Id = id;
                Name = name;
            }
        }

        // ---- the call boundary --------------------------------------------- //

        /// <summary>
        /// Record the entry of a call and start its clock.
        ///
        /// Argument reprs are taken here, before the body runs, so a method that
        /// mutates its inputs still logs the values it was actually called with.
        /// </summary>
        public static Ctx Enter(string name, object[] args)
        {
            // A real UUIDv4 from the platform generator, never seeded from the
            // clock: two calls in the same millisecond must not collide.
            var ctx = new Ctx(Guid.NewGuid().ToString(), name);
            string rendered = JoinReprs(args);
            WriteLine(EntryRecord(NowStamp(), ctx.Id, ThreadToken(), name, rendered));
            // Monotonic, and started AFTER the entry write so the sink's own cost
            // is not charged to the call being measured (SPEC.md section 4).
            ctx.Started = Stopwatch.GetTimestamp();
            return ctx;
        }

        /// <summary>
        /// Note the value a `return` is carrying, and hand it straight back.
        ///
        /// The repr is taken here, the instant the body returned, so a `finally`
        /// in the observed method cannot rewrite what gets logged. The record
        /// itself is written by <see cref="ExitPending"/> in the outermost
        /// `finally`, so the reported duration covers the method's own `finally`
        /// blocks too — the same span the JavaScript backend reports.
        ///
        /// The type argument is always spelled out at the call site rather than
        /// inferred: `return null;`, `return () =&gt; 1;` and `return [1, 2];` have
        /// no type of their own and would fail inference with CS0411.
        /// </summary>
        public static T Ret<T>(Ctx ctx, T value)
        {
            if (ctx != null)
            {
                ctx.Pending = Repr(value);
            }
            return value;
        }

        /// <summary>Note the return of a `void` method.</summary>
        public static void RetVoid(Ctx ctx)
        {
            if (ctx != null)
            {
                ctx.Pending = "(no value)";
            }
        }

        /// <summary>
        /// Record a call that left by throwing. The caller rethrows with a bare
        /// `throw;`, so control flow and the origin of the exception are unchanged.
        /// </summary>
        public static void ExitThrow(Ctx ctx, Exception error)
        {
            Complete(ctx, "x", Describe(error));
        }

        /// <summary>
        /// Write the completion record for a call that is leaving normally. Called
        /// from the outermost `finally`; does nothing when the call already recorded
        /// a completion, which is what happens when it left by throwing.
        /// </summary>
        public static void ExitPending(Ctx ctx)
        {
            if (ctx != null)
            {
                Complete(ctx, "r", ctx.Pending);
            }
        }

        private static void Complete(Ctx ctx, string field, string value)
        {
            if (ctx == null || ctx.Done)
            {
                return;
            }
            double seconds = (Stopwatch.GetTimestamp() - ctx.Started)
                             / (double)Stopwatch.Frequency;
            ctx.Done = true;
            WriteLine(OutRecord(ctx.Id, ctx.Name, field, value, seconds));
        }

        // ---- rendering ------------------------------------------------------ //

        /// <summary>"&lt;fully qualified type&gt;: &lt;message&gt;" — both halves, always.</summary>
        internal static string Describe(Exception error)
        {
            if (error == null)
            {
                return "System.Exception: (null)";
            }
            string message;
            try
            {
                message = error.Message;
            }
            catch (Exception)
            {
                message = null;
            }
            string type = error.GetType().FullName ?? error.GetType().Name;
            return type + ": " + (message == null ? "(no message)" : message);
        }

        internal static string JoinReprs(object[] args)
        {
            if (args == null || args.Length == 0)
            {
                return "";
            }
            var text = new StringBuilder();
            for (int i = 0; i < args.Length; i++)
            {
                if (i > 0)
                {
                    text.Append(", ");
                }
                text.Append(Repr(args[i]));
            }
            return text.ToString();
        }

        /// <summary>
        /// Short rendering of one value.
        ///
        /// Every path is guarded: a ToString() that throws must not turn a working
        /// program into a crashing one just because it is being observed.
        /// </summary>
        public static string Repr(object value)
        {
            string text;
            try
            {
                text = Render(value);
            }
            catch (Exception e)
            {
                string type = value == null ? "null" : (value.GetType().FullName ?? "?");
                text = "<" + type + " ToString threw " + e.GetType().FullName + ">";
            }
            if (CodePointCount(text) > MaxRepr)
            {
                text = TakeCodePoints(text, MaxRepr) + Ellipsis;
            }
            return text;
        }

        private static string Render(object value)
        {
            switch (value)
            {
                case null:
                    return "null";
                case string s:
                    return Quote(s);
                case char c:
                    return "'" + c + "'";
                case bool b:
                    return b ? "true" : "false";
                case Array a:
                    return RenderArray(a);
            }
            // Culture must not leak into the trace: on a ru_RU host the default
            // ToString() of a double writes "1,5", which is a different record for
            // the same run.
            if (value is IFormattable formattable)
            {
                return formattable.ToString(null, CultureInfo.InvariantCulture);
            }
            return value.ToString() ?? "null";
        }

        private static string RenderArray(Array array)
        {
            if (array.Rank != 1)
            {
                return array.ToString() ?? "null";
            }
            var text = new StringBuilder("[");
            int index = 0;
            foreach (object item in array)
            {
                if (index++ > 0)
                {
                    text.Append(", ");
                }
                text.Append(item is Array nested ? RenderArray(nested) : Render(item));
            }
            return text.Append(']').ToString();
        }

        // ---- the record ------------------------------------------------------ //

        internal static string EntryRecord(string stamp, string id, string thread,
                                           string name, string args)
        {
            var fields = new Dictionary<string, string> { { "a", args }, { "k", "" } };
            return Bounded(fields, f =>
                "{\"p\":\"in\",\"t\":" + Quote(stamp)
                + ",\"id\":" + Quote(id)
                + ",\"ci\":-1,\"th\":" + Quote(thread)
                + ",\"fn\":" + Quote(name)
                + ",\"a\":" + Quote(f["a"])
                + ",\"k\":" + Quote(f["k"]) + "}");
        }

        internal static string OutRecord(string id, string name, string field,
                                         string value, double seconds)
        {
            var fields = new Dictionary<string, string> { { field, value } };
            string rendered = seconds.ToString("F6", CultureInfo.InvariantCulture);
            return Bounded(fields, f =>
                "{\"p\":\"out\",\"id\":" + Quote(id)
                + ",\"fn\":" + Quote(name)
                + ",\"" + field + "\":" + Quote(f[field])
                + ",\"d\":" + rendered + "}");
        }

        /// <summary>Builds a record, halving its longest value field until the line fits.</summary>
        private static string Bounded(Dictionary<string, string> fields,
                                      Func<Dictionary<string, string>, string> build)
        {
            string line = build(fields) + "\n";
            if (Utf8.GetByteCount(line) <= MaxRecordBytes)
            {
                return line;
            }
            var shrunk = new Dictionary<string, string>(fields);
            var trimmed = new List<string>();
            int budget = MaxRecordBytes - 64;   // headroom for the ellipsis markers
            for (int step = 0; step < 64; step++)
            {
                string field = Longest(shrunk);
                string value;
                if (!shrunk.TryGetValue(field, out value) || string.IsNullOrEmpty(value))
                {
                    break;
                }
                // Cut on a code point boundary: half a character would make the
                // whole line undecodable, which is worse than a value that is too
                // long.
                shrunk[field] = TakeCodePoints(value, CodePointCount(value) / 2);
                if (!trimmed.Contains(field))
                {
                    trimmed.Add(field);
                }
                if (Utf8.GetByteCount(build(shrunk) + "\n") <= budget)
                {
                    break;
                }
            }
            foreach (string field in trimmed)
            {
                shrunk[field] = shrunk[field] + Ellipsis;
            }
            return build(shrunk) + "\n";
        }

        private static string Longest(Dictionary<string, string> fields)
        {
            string best = Shrinkable[0];
            foreach (string field in Shrinkable)
            {
                string value;
                string current;
                fields.TryGetValue(field, out value);
                fields.TryGetValue(best, out current);
                if (value != null && (current == null || value.Length > current.Length))
                {
                    best = field;
                }
            }
            return best;
        }

        // ---- code points ------------------------------------------------------ //

        /// <summary>
        /// How many code points a string holds. .NET strings are UTF-16, so a
        /// character outside the basic plane counts as two `char`s and one code
        /// point — the limits in SPEC.md are stated in the latter.
        /// </summary>
        internal static int CodePointCount(string text)
        {
            int count = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length
                    && char.IsLowSurrogate(text[i + 1]))
                {
                    i++;
                }
                count++;
            }
            return count;
        }

        /// <summary>The first <paramref name="max"/> code points of a string.</summary>
        internal static string TakeCodePoints(string text, int max)
        {
            int count = 0;
            for (int i = 0; i < text.Length; i++)
            {
                if (count == max)
                {
                    return text.Substring(0, i);
                }
                if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length
                    && char.IsLowSurrogate(text[i + 1]))
                {
                    i++;
                }
                count++;
            }
            return text;
        }

        // ---- the sink --------------------------------------------------------- //

        private static string DebugInfoPath()
        {
            string path = Environment.GetEnvironmentVariable("OUROBOROS_DEBUG_INFO");
            return string.IsNullOrEmpty(path) ? "debug.info" : path;
        }

        private static string NowStamp()
        {
            // Local time, milliseconds, no zone — SPEC.md's `t`.
            return DateTime.Now.ToString("yyyy-MM-dd'T'HH:mm:ss.fff",
                                         CultureInfo.InvariantCulture);
        }

        /// <summary>
        /// "&lt;pid&gt;.&lt;thread&gt;" — both halves. A token that names only the
        /// process cannot tell two threads apart, and one that names only the
        /// thread cannot tell two processes sharing one debug.info apart.
        /// </summary>
        private static string ThreadToken()
        {
            return Environment.ProcessId.ToString(CultureInfo.InvariantCulture) + "."
                   + Environment.CurrentManagedThreadId.ToString(CultureInfo.InvariantCulture);
        }

        /// <summary>
        /// One record, one append. The handle is opened once per path and kept, so
        /// a call does not pay for opening a file; FileMode.Append is O_APPEND and
        /// the 1-byte buffer keeps the runtime from splitting or delaying the write,
        /// so each record lands whole.
        /// </summary>
        private static void WriteLine(string line)
        {
            byte[] bytes = Utf8.GetBytes(line);
            string path = DebugInfoPath();
            try
            {
                lock (WriteLock)
                {
                    FileStream sink;
                    if (!Sinks.TryGetValue(path, out sink))
                    {
                        sink = new FileStream(path, FileMode.Append, FileAccess.Write,
                                              FileShare.ReadWrite, 1);
                        Sinks[path] = sink;
                    }
                    sink.Write(bytes, 0, bytes.Length);
                    sink.Flush();
                }
            }
            catch (Exception)
            {
                // A sink that cannot be written must not take the observed program
                // down with it.
            }
        }

        /// <summary>JSON string literal with the mandatory escaping.</summary>
        internal static string Quote(string text)
        {
            var output = new StringBuilder(text.Length + 2).Append('"');
            foreach (char c in text)
            {
                switch (c)
                {
                    case '"': output.Append("\\\""); break;
                    case '\\': output.Append("\\\\"); break;
                    case '\n': output.Append("\\n"); break;
                    case '\r': output.Append("\\r"); break;
                    case '\t': output.Append("\\t"); break;
                    case '\b': output.Append("\\b"); break;
                    case '\f': output.Append("\\f"); break;
                    default:
                        if (c < 0x20)
                        {
                            output.Append("\\u").Append(((int)c).ToString(
                                "x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            output.Append(c);
                        }
                        break;
                }
            }
            return output.Append('"').ToString();
        }
    }
}
