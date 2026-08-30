package ouroboros;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Runtime logging helper injected into instrumented Java code.
 *
 * <p>The Java analogue of {@code ouroboros_runtime.py}. It uses nothing outside
 * the JDK, so it can be copied into a draft project and keep working after the
 * draft is synced. It appends JSONL records to the file named by
 * {@code OUROBOROS_DEBUG_INFO} in the exact format SPEC.md defines — never via
 * stdout.
 *
 * <p>Two lines per call, paired by {@code id}:
 * <pre>
 * {"p":"in","t":"&lt;iso&gt;","id":"&lt;uuid&gt;","ci":-1,"th":"&lt;pid.thread&gt;","fn":"&lt;name&gt;","a":"&lt;args&gt;","k":""}
 * {"p":"out","id":"&lt;uuid&gt;","fn":"&lt;name&gt;","r":"&lt;repr&gt;","d":&lt;seconds&gt;}
 * {"p":"out","id":"&lt;uuid&gt;","fn":"&lt;name&gt;","x":"&lt;Type: message&gt;","d":&lt;seconds&gt;}
 * </pre>
 *
 * <p>Instrumented code calls:
 * <pre>
 * var c = ouroboros.OuroborosRuntime.enter("Cls.m", new Object[]{a, b});
 * try { return ouroboros.OuroborosRuntime.ret(c, expr); }
 * catch (Throwable e) { ouroboros.OuroborosRuntime.exitThrow(c, e); throw e; }
 * finally { ouroboros.OuroborosRuntime.exitPending(c); }
 * </pre>
 */
public final class OuroborosRuntime {

    /** Cap on one rendered value, matching the other backends' short reprs. */
    private static final int MAX_REPR = 200;

    /**
     * Hard ceiling on one record, in bytes including the newline. PIPE_BUF is
     * 4096 on Linux and SPEC.md §1 promises each record is written with a single
     * append and stays under it — that promise is what lets several processes
     * share one debug.info. The per-value cap alone does not deliver it: a call
     * with thirty long arguments overruns it, the kernel tears the line, and the
     * parser counts both halves as malformed and drops them.
     */
    private static final int MAX_RECORD_BYTES = 4096;

    /**
     * Fields that may be shortened to fit. {@code fn}/{@code id}/{@code t} are
     * what makes a torn record identifiable at all, so they are never touched.
     */
    private static final String[] SHRINKABLE = {"a", "k", "r", "x"};

    private static final String ELLIPSIS = "…";

    private static final DateTimeFormatter STAMP =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS", Locale.ROOT);

    private static final Map<String, FileChannel> CHANNELS = new HashMap<>();

    private static final Object WRITE_LOCK = new Object();

    /** One in-flight call: the {@code in} record is already written, the {@code out} is not. */
    public static final class Ctx {
        private final String id;
        private final String name;
        private long started;
        private boolean done;

        private Ctx(String id, String name) {
            this.id = id;
            this.name = name;
        }
    }

    // ---- the call boundary ------------------------------------------------ //

    /**
     * Record the entry of a call and start its clock.
     *
     * <p>Argument reprs are taken here, before the body runs, so a method that
     * mutates its inputs still logs the values it was actually called with.
     */
    public static Ctx enter(String name, Object[] args) {
        Ctx ctx = new Ctx(UUID.randomUUID().toString(), name);
        String rendered = joinReprs(args);
        writeln(entryRecord(nowStamp(), ctx.id, threadToken(), name, rendered));
        // Monotonic, and started AFTER the entry write so the sink's own cost is
        // not charged to the call being measured (SPEC.md §4).
        ctx.started = System.nanoTime();
        return ctx;
    }

    /**
     * Write the completion record for a call leaving with {@code value}.
     *
     * <p>Called from the outermost {@code finally} with the temp the instrumented
     * body assigned every {@code return} into, so the reported duration covers the
     * method's own {@code finally} blocks — the same span the JavaScript backend
     * reports. Does nothing when the call already recorded a completion, which is
     * what happens when it left by throwing.
     */
    public static void exit(Ctx ctx, Object value) {
        complete(ctx, "r", repr(value));
    }

    /** Completion record for a {@code void} method or a constructor. */
    public static void exitVoid(Ctx ctx) {
        complete(ctx, "r", "(no value)");
    }

    /** Record a call that left by throwing. The caller rethrows; control flow is unchanged. */
    public static void exitThrow(Ctx ctx, Throwable error) {
        complete(ctx, "x", describe(error));
    }

    private static void complete(Ctx ctx, String field, String value) {
        if (ctx == null || ctx.done) {
            return;
        }
        double seconds = (System.nanoTime() - ctx.started) / 1e9;
        ctx.done = true;
        writeln(outRecord(ctx.id, ctx.name, field, value, seconds));
    }

    // ---- rendering -------------------------------------------------------- //

    /** {@code "<fully qualified type>: <message>"} — both halves, always. */
    static String describe(Throwable error) {
        if (error == null) {
            return "java.lang.Throwable: (null)";
        }
        String message;
        try {
            message = error.getMessage();
        } catch (Throwable ignored) {
            message = null;
        }
        return error.getClass().getName() + ": " + (message == null ? "(no message)" : message);
    }

    static String joinReprs(Object[] args) {
        if (args == null || args.length == 0) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < args.length; i++) {
            if (i > 0) {
                out.append(", ");
            }
            out.append(repr(args[i]));
        }
        return out.toString();
    }

    /**
     * Short rendering of one value.
     *
     * <p>Every path is guarded: a {@code toString()} that throws must not turn a
     * working program into a crashing one just because it is being observed.
     */
    public static String repr(Object value) {
        String text;
        try {
            text = render(value);
        } catch (Throwable e) {
            text = "<" + value.getClass().getName() + " toString threw "
                    + e.getClass().getName() + ">";
        }
        if (text.codePointCount(0, text.length()) > MAX_REPR) {
            text = text.substring(0, text.offsetByCodePoints(0, MAX_REPR)) + ELLIPSIS;
        }
        return text;
    }

    private static String render(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof String s) {
            return quote(s);
        }
        if (value instanceof Character c) {
            return "'" + c + "'";
        }
        if (value instanceof Object[] a) {
            return Arrays.deepToString(a);
        }
        if (value instanceof int[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof long[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof double[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof float[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof short[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof byte[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof char[] a) {
            return Arrays.toString(a);
        }
        if (value instanceof boolean[] a) {
            return Arrays.toString(a);
        }
        return String.valueOf(value);
    }

    // ---- the record ------------------------------------------------------- //

    static String entryRecord(String stamp, String id, String thread,
                              String name, String args) {
        Map<String, String> strings = new HashMap<>();
        strings.put("a", args);
        strings.put("k", "");
        return bounded(strings, fields ->
                "{\"p\":\"in\",\"t\":" + quote(stamp)
                        + ",\"id\":" + quote(id)
                        + ",\"ci\":-1,\"th\":" + quote(thread)
                        + ",\"fn\":" + quote(name)
                        + ",\"a\":" + quote(fields.get("a"))
                        + ",\"k\":" + quote(fields.get("k")) + "}");
    }

    static String outRecord(String id, String name, String field, String value, double seconds) {
        Map<String, String> strings = new HashMap<>();
        strings.put(field, value);
        String rendered = String.format(Locale.ROOT, "%.6f", seconds);
        return bounded(strings, fields ->
                "{\"p\":\"out\",\"id\":" + quote(id)
                        + ",\"fn\":" + quote(name)
                        + ",\"" + field + "\":" + quote(fields.get(field))
                        + ",\"d\":" + rendered + "}");
    }

    /** Builds a record, halving its longest value field until the line fits. */
    private static String bounded(Map<String, String> fields,
                                  java.util.function.Function<Map<String, String>, String> build) {
        String line = build.apply(fields) + "\n";
        if (line.getBytes(StandardCharsets.UTF_8).length <= MAX_RECORD_BYTES) {
            return line;
        }
        Map<String, String> shrunk = new HashMap<>(fields);
        List<String> trimmed = new ArrayList<>();
        int budget = MAX_RECORD_BYTES - 64;  // headroom for the ellipsis markers
        for (int step = 0; step < 64; step++) {
            String field = longest(shrunk);
            String value = shrunk.get(field);
            if (value == null || value.isEmpty()) {
                break;
            }
            // Cut on a code point boundary: half a character would make the whole
            // line undecodable, which is worse than a value that is too long.
            int half = value.codePointCount(0, value.length()) / 2;
            shrunk.put(field, value.substring(0, value.offsetByCodePoints(0, half)));
            if (!trimmed.contains(field)) {
                trimmed.add(field);
            }
            if ((build.apply(shrunk) + "\n").getBytes(StandardCharsets.UTF_8).length <= budget) {
                break;
            }
        }
        for (String field : trimmed) {
            shrunk.put(field, shrunk.get(field) + ELLIPSIS);
        }
        return build.apply(shrunk) + "\n";
    }

    private static String longest(Map<String, String> fields) {
        String best = SHRINKABLE[0];
        for (String field : SHRINKABLE) {
            String value = fields.get(field);
            String current = fields.get(best);
            if (value != null && (current == null || value.length() > current.length())) {
                best = field;
            }
        }
        return best;
    }

    // ---- the sink --------------------------------------------------------- //

    private static String debugInfoPath() {
        String path = System.getenv("OUROBOROS_DEBUG_INFO");
        return path == null || path.isEmpty() ? "debug.info" : path;
    }

    private static String nowStamp() {
        return LocalDateTime.now().format(STAMP);
    }

    /**
     * {@code "<pid>.<thread>"} — both halves. A token that names only the process
     * cannot tell two threads apart, and one that names only the thread cannot
     * tell two processes sharing one debug.info apart.
     */
    private static String threadToken() {
        return ProcessHandle.current().pid() + "." + Thread.currentThread().threadId();
    }

    /**
     * One record, one append. The channel is opened once per path and kept, so a
     * call does not pay for opening a file; APPEND makes each write land whole.
     */
    private static void writeln(String line) {
        byte[] bytes = line.getBytes(StandardCharsets.UTF_8);
        String path = debugInfoPath();
        try {
            synchronized (WRITE_LOCK) {
                FileChannel channel = CHANNELS.get(path);
                if (channel == null || !channel.isOpen()) {
                    channel = FileChannel.open(Path.of(path), StandardOpenOption.CREATE,
                            StandardOpenOption.WRITE, StandardOpenOption.APPEND);
                    CHANNELS.put(path, channel);
                }
                channel.write(ByteBuffer.wrap(bytes));
            }
        } catch (IOException ignored) {
            // A sink that cannot be written must not take the observed program
            // down with it.
        }
    }

    /** JSON string literal with the mandatory escaping. */
    static String quote(String text) {
        StringBuilder out = new StringBuilder(text.length() + 2).append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    private OuroborosRuntime() {
    }
}
