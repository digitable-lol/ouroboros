/* Ouroboros C/C++ range-emitter.
 *
 * Reads source code on stdin, parses it with libclang, and prints a JSON
 * description of every instrumentable function definition in the main file:
 * where its body starts and ends, its parameters (with the printf specifier
 * each static type needs), what its `return`s hand back, and where those
 * returned expressions sit. It performs NO code generation -- the Python side
 * does the splicing. This is the same contract the JavaScript backend uses with
 * `_js/emitter.js`, and it keeps the parser integration a thin,
 * Elixir-port-friendly helper.
 *
 * Usage:  emitter <c|cpp> <filename> [clang args...]
 *   stdin  -- the source buffer (handed to libclang as an unsaved file)
 *   stdout -- {"ok":true,"errorCount":N,"errors":[...],"functions":[...]}
 *             or {"ok":false,"error":"..."}
 *
 * All offsets are BYTE offsets into the buffer read from stdin, taken at the
 * EXPANSION location (what clang_getExpansionLocation reports), so a function
 * produced by a macro reports offsets inside the macro invocation -- the Python
 * side detects that case by looking at the byte it was pointed at.
 */

#ifdef OURO_SYSTEM_CLANG_C
#include <clang-c/Index.h>
#else
#include "libclang_api.h"
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef OURO_SYSTEM_CLANG_C
/* The control build links libclang the ordinary way, so there is nothing to
 * load. It exists to check the vendored declarations against the real header --
 * see tests/test_clangbridge.py. */
static const char *ouro_load_libclang(void) { return NULL; }
#endif

/* Cap on how many error diagnostics are spelled out. The Python side needs the
 * true count (it reports it) and the first few messages (it shows a sample and
 * raises with the first), never the whole list -- a parse of a real tree file
 * can carry hundreds. */
#define MAX_ERROR_MESSAGES 5

/* --------------------------------------------------------------------------
 * growable output buffer
 * -------------------------------------------------------------------------- */

typedef struct {
    char *p;
    size_t len;
    size_t cap;
} Buf;

static void die_oom(void)
{
    fputs("{\"ok\":false,\"error\":\"out of memory\"}", stdout);
    exit(1);
}

static void buf_need(Buf *b, size_t extra)
{
    if (b->len + extra + 1 <= b->cap)
        return;
    size_t cap = b->cap ? b->cap : 4096;
    while (cap < b->len + extra + 1)
        cap *= 2;
    char *np = realloc(b->p, cap);
    if (!np)
        die_oom();
    b->p = np;
    b->cap = cap;
}

static void buf_putn(Buf *b, const char *s, size_t n)
{
    buf_need(b, n);
    memcpy(b->p + b->len, s, n);
    b->len += n;
    b->p[b->len] = '\0';
}

static void buf_puts(Buf *b, const char *s) { buf_putn(b, s, strlen(s)); }

static void buf_putc(Buf *b, char c) { buf_putn(b, &c, 1); }

static void buf_putu(Buf *b, unsigned v)
{
    char tmp[24];
    int n = snprintf(tmp, sizeof tmp, "%u", v);
    buf_putn(b, tmp, (size_t)n);
}

/* JSON string literal, quotes included. Bytes >= 0x20 other than " and \ pass
 * through untouched, so UTF-8 in an identifier survives verbatim. */
static void buf_json(Buf *b, const char *s)
{
    buf_putc(b, '"');
    for (const unsigned char *q = (const unsigned char *)s; *q; q++) {
        switch (*q) {
        case '"':  buf_puts(b, "\\\""); break;
        case '\\': buf_puts(b, "\\\\"); break;
        case '\n': buf_puts(b, "\\n"); break;
        case '\r': buf_puts(b, "\\r"); break;
        case '\t': buf_puts(b, "\\t"); break;
        default:
            if (*q < 0x20) {
                char tmp[8];
                snprintf(tmp, sizeof tmp, "\\u%04x", *q);
                buf_puts(b, tmp);
            } else {
                buf_putc(b, (char)*q);
            }
        }
    }
    buf_putc(b, '"');
}

/* --------------------------------------------------------------------------
 * libclang string / location helpers
 * -------------------------------------------------------------------------- */

static char *cx_dup(CXString s)
{
    const char *c = clang_getCString(s);
    char *out = strdup(c ? c : "");
    if (!out)
        die_oom();
    clang_disposeString(s);
    return out;
}

static void buf_json_cx(Buf *b, CXString s)
{
    const char *c = clang_getCString(s);
    buf_json(b, c ? c : "");
    clang_disposeString(s);
}

static unsigned loc_offset(CXSourceLocation l)
{
    unsigned off = 0;
    clang_getExpansionLocation(l, NULL, NULL, NULL, &off);
    return off;
}

/* Name of the file a location expands into, or NULL for a location with no
 * file (builtin / command-line). Caller frees. */
static char *loc_file(CXSourceLocation l)
{
    CXFile f = NULL;
    clang_getExpansionLocation(l, &f, NULL, NULL, NULL);
    if (!f)
        return NULL;
    return cx_dup(clang_getFileName(f));
}

static int in_main_file(CXCursor c, const char *main_file)
{
    char *name = loc_file(clang_getCursorLocation(c));
    if (!name)
        return 0;
    int same = strcmp(name, main_file) == 0;
    free(name);
    return same;
}

/* --------------------------------------------------------------------------
 * type -> printf specifier
 *
 * The one piece of parser knowledge the C backend cannot splice without: which
 * printf conversion a parameter's static type needs. Reporting it here is what
 * lets the Python side build the format string with no type handling of its own.
 * -------------------------------------------------------------------------- */

static const char *scalar_spec(int kind)
{
    switch (kind) {
    case CXType_Int: case CXType_Enum: case CXType_Short: case CXType_SChar:
    case CXType_Char_S: case CXType_Bool: case CXType_WChar:
        return "%d";
    case CXType_UInt: case CXType_UShort: case CXType_UChar: case CXType_Char_U:
        return "%u";
    case CXType_Long:       return "%ld";
    case CXType_ULong:      return "%lu";
    case CXType_LongLong:   return "%lld";
    case CXType_ULongLong:  return "%llu";
    case CXType_Float:      return "%f";
    case CXType_Double:     return "%f";
    case CXType_LongDouble: return "%Lf";
    default:                return NULL;
    }
}

/* spec = printf conversion or NULL (unprintable); is_string = `const char *`,
 * which the caller must guard against NULL before handing it to %s. */
static void spec_for(CXType t, const char **spec, int *is_string)
{
    CXType canon = clang_getCanonicalType(t);
    *is_string = 0;
    if (canon.kind == CXType_Pointer) {
        CXType pointee = clang_getPointeeType(canon);
        CXType pcanon = clang_getCanonicalType(pointee);
        int chars = pcanon.kind == CXType_Char_S || pcanon.kind == CXType_Char_U ||
                    pcanon.kind == CXType_SChar || pcanon.kind == CXType_UChar;
        if (chars && clang_isConstQualifiedType(pointee)) {
            *spec = "%s";
            *is_string = 1;
            return;
        }
        *spec = "%p";
        return;
    }
    *spec = scalar_spec(canon.kind);
}

/* C type string for the `__ouro_result` temp, with top-level const/volatile
 * stripped (you cannot assign to a const). NULL when it cannot be made
 * assignable safely. Caller frees. */
static char *temp_type(CXType t)
{
    char *s = cx_dup(clang_getTypeSpelling(t));
    int is_const = clang_isConstQualifiedType(t) != 0;
    int is_volatile = clang_isVolatileQualifiedType(t) != 0;
    if (!is_const && !is_volatile)
        return s;
    if (strncmp(s, "const ", 6) == 0)
        memmove(s, s + 6, strlen(s + 6) + 1);
    if (strncmp(s, "volatile ", 9) == 0)
        memmove(s, s + 9, strlen(s + 9) + 1);
    if (is_const) {
        const char *star = strchr(s, '*');
        size_t head = star ? (size_t)(star - s) : strlen(s);
        for (size_t i = 0; i + 5 <= head; i++) {
            if (strncmp(s + i, "const", 5) == 0) {
                free(s); /* a const we could not strip -- do not capture */
                return NULL;
            }
        }
    }
    return s;
}

/* --------------------------------------------------------------------------
 * traversal
 * -------------------------------------------------------------------------- */

typedef struct {
    Buf *out;
    CXTranslationUnit tu;
    const char *main_file;
    int cpp;      /* 1 for the C++ dialect, 0 for C */
    int emitted;  /* functions written so far (drives the JSON comma) */
} Ctx;

/* First child of `parent` (preorder), or a null-kind cursor when it has none. */
static enum CXChildVisitResult grab_first(CXCursor c, CXCursor parent, CXClientData data)
{
    (void)parent;
    *(CXCursor *)data = c;
    return CXChildVisit_Break;
}

typedef struct {
    CXCursor found;
    int have;
    int kind;
} FindKind;

static enum CXChildVisitResult grab_kind(CXCursor c, CXCursor parent, CXClientData data)
{
    (void)parent;
    FindKind *f = (FindKind *)data;
    if ((int)clang_getCursorKind(c) == f->kind) {
        f->found = c;
        f->have = 1;
        return CXChildVisit_Break;
    }
    return CXChildVisit_Continue;
}

/* The function's own body: its first direct CompoundStmt child. */
static int body_of(CXCursor fn, CXCursor *body)
{
    FindKind f = {.have = 0, .kind = CXCursor_CompoundStmt};
    clang_visitChildren(fn, grab_kind, &f);
    if (!f.have)
        return 0;
    *body = f.found;
    return 1;
}

/* ---- returns ------------------------------------------------------------ */

typedef struct {
    Buf *out;
    int count;
} RetCtx;

static enum CXChildVisitResult visit_returns(CXCursor c, CXCursor parent, CXClientData data)
{
    (void)parent;
    RetCtx *rc = (RetCtx *)data;
    int kind = clang_getCursorKind(c);
    /* A lambda's `return`s belong to the lambda, not to the function that
     * spells it out, so its subtree is not descended into. C has no lambdas,
     * which is why the two languages can share this walk. */
    if (kind == CXCursor_LambdaExpr)
        return CXChildVisit_Continue;
    if (kind == CXCursor_ReturnStmt) {
        CXCursor value;
        value.kind = 0;
        int have_value = 0;
        CXCursor probe = value;
        if (clang_visitChildren(c, grab_first, &probe)) {
            value = probe;
            have_value = 1;
        }
        if (rc->count++)
            buf_putc(rc->out, ',');
        buf_puts(rc->out, "{\"argStart\":");
        if (have_value) {
            CXSourceRange ext = clang_getCursorExtent(value);
            buf_putu(rc->out, loc_offset(clang_getRangeStart(ext)));
            buf_puts(rc->out, ",\"argEnd\":");
            buf_putu(rc->out, loc_offset(clang_getRangeEnd(ext)));
            buf_puts(rc->out, ",\"isInitList\":");
            buf_puts(rc->out,
                     clang_getCursorKind(value) == CXCursor_InitListExpr ? "true" : "false");
        } else {
            buf_puts(rc->out, "null,\"argEnd\":null,\"isInitList\":false");
        }
        buf_putc(rc->out, '}');
    }
    return CXChildVisit_Recurse;
}

/* ---- constexpr ---------------------------------------------------------- */

/* True if the declaration carries `constexpr` / `consteval`, read off the
 * tokens ahead of the body: libclang exposes no query for it. */
static int is_constexpr(CXTranslationUnit tu, CXCursor fn, CXCursor body)
{
    unsigned limit = loc_offset(clang_getRangeStart(clang_getCursorExtent(body)));
    CXToken *tokens = NULL;
    unsigned n = 0;
    clang_tokenize(tu, clang_getCursorExtent(fn), &tokens, &n);
    int found = 0;
    for (unsigned i = 0; i < n; i++) {
        if (loc_offset(clang_getTokenLocation(tu, tokens[i])) >= limit)
            break;
        char *sp = cx_dup(clang_getTokenSpelling(tu, tokens[i]));
        if (strcmp(sp, "constexpr") == 0 || strcmp(sp, "consteval") == 0)
            found = 1;
        free(sp);
        if (found)
            break;
    }
    if (tokens)
        clang_disposeTokens(tu, tokens, n);
    return found;
}

/* ---- qualified name ----------------------------------------------------- */

/* `ns::Class::method` for C++; the plain spelling for C. Written into `out`
 * as a JSON string. */
static void put_qualified_name(Buf *out, CXCursor fn, int cpp)
{
    char *parts[32];
    int n = 0;
    parts[n++] = cx_dup(clang_getCursorSpelling(fn));
    if (cpp) {
        CXCursor p = clang_getCursorSemanticParent(fn);
        while (n < 32) {
            int k = clang_getCursorKind(p);
            if (k != CXCursor_Namespace && k != CXCursor_ClassDecl &&
                k != CXCursor_StructDecl && k != CXCursor_ClassTemplate &&
                k != CXCursor_UnionDecl)
                break;
            char *s = cx_dup(clang_getCursorSpelling(p));
            if (*s)
                parts[n++] = s;
            else
                free(s);
            p = clang_getCursorSemanticParent(p);
        }
    }
    size_t total = 1;
    for (int i = 0; i < n; i++)
        total += strlen(parts[i]) + 2;
    char *joined = malloc(total);
    if (!joined)
        die_oom();
    joined[0] = '\0';
    for (int i = n - 1; i >= 0; i--) {
        strcat(joined, parts[i]);
        if (i)
            strcat(joined, "::");
        free(parts[i]);
    }
    buf_json(out, joined);
    free(joined);
}

/* ---- one function ------------------------------------------------------- */

static void emit_function(Ctx *ctx, CXCursor fn, CXCursor body)
{
    Buf *out = ctx->out;
    if (ctx->emitted++)
        buf_putc(out, ',');

    buf_puts(out, "{\"name\":");
    buf_json_cx(out, clang_getCursorSpelling(fn));
    buf_puts(out, ",\"qualifiedName\":");
    put_qualified_name(out, fn, ctx->cpp);

    CXSourceRange fn_ext = clang_getCursorExtent(fn);
    CXSourceRange body_ext = clang_getCursorExtent(body);
    buf_puts(out, ",\"extentStart\":");
    buf_putu(out, loc_offset(clang_getRangeStart(fn_ext)));
    buf_puts(out, ",\"bodyStart\":");
    buf_putu(out, loc_offset(clang_getRangeStart(body_ext)));
    buf_puts(out, ",\"bodyEnd\":");
    buf_putu(out, loc_offset(clang_getRangeEnd(body_ext)));

    buf_puts(out, ",\"isConstexpr\":");
    buf_puts(out, (ctx->cpp && is_constexpr(ctx->tu, fn, body)) ? "true" : "false");

    /* ---- parameters ---- */
    buf_puts(out, ",\"params\":[");
    int nargs = clang_Cursor_getNumArguments(fn);
    for (int i = 0; i < nargs; i++) {
        CXCursor arg = clang_Cursor_getArgument(fn, (unsigned)i);
        if (i)
            buf_putc(out, ',');
        buf_puts(out, "{\"name\":");
        buf_json_cx(out, clang_getCursorSpelling(arg));
        const char *spec = NULL;
        int is_string = 0;
        spec_for(clang_getCursorType(arg), &spec, &is_string);
        buf_puts(out, ",\"spec\":");
        if (spec)
            buf_json(out, spec);
        else
            buf_puts(out, "null");
        buf_puts(out, ",\"isString\":");
        buf_puts(out, is_string ? "true" : "false");
        buf_putc(out, '}');
    }
    buf_puts(out, "]");

    /* ---- result ---- */
    CXType result = clang_getCursorResultType(fn);
    CXType result_canon = clang_getCanonicalType(result);
    const char *rspec = NULL;
    int rstring = 0;
    spec_for(result, &rspec, &rstring);
    buf_puts(out, ",\"result\":{\"isVoid\":");
    buf_puts(out, result_canon.kind == CXType_Void ? "true" : "false");
    buf_puts(out, ",\"isRecord\":");
    buf_puts(out, result_canon.kind == CXType_Record ? "true" : "false");
    buf_puts(out, ",\"spec\":");
    if (rspec)
        buf_json(out, rspec);
    else
        buf_puts(out, "null");
    buf_puts(out, ",\"isString\":");
    buf_puts(out, rstring ? "true" : "false");
    buf_puts(out, ",\"tempType\":");
    if (result_canon.kind == CXType_Void) {
        buf_puts(out, "null");
    } else {
        char *tt = temp_type(result);
        if (tt) {
            buf_json(out, tt);
            free(tt);
        } else {
            buf_puts(out, "null");
        }
    }
    buf_putc(out, '}');

    /* ---- returns ---- */
    buf_puts(out, ",\"returns\":[");
    RetCtx rc = {.out = out, .count = 0};
    clang_visitChildren(fn, visit_returns, &rc);
    buf_puts(out, "]}");
}

static enum CXChildVisitResult visit_all(CXCursor c, CXCursor parent, CXClientData data)
{
    (void)parent;
    Ctx *ctx = (Ctx *)data;
    int kind = clang_getCursorKind(c);
    int wanted = kind == CXCursor_FunctionDecl ||
                 (ctx->cpp && kind == CXCursor_CXXMethod);
    if (wanted && clang_isCursorDefinition(c) && in_main_file(c, ctx->main_file)) {
        CXCursor body;
        if (body_of(c, &body))
            emit_function(ctx, c, body);
    }
    return CXChildVisit_Recurse;
}

/* --------------------------------------------------------------------------
 * main
 * -------------------------------------------------------------------------- */

static char *read_stdin(size_t *len_out)
{
    size_t cap = 1 << 16, len = 0;
    char *buf = malloc(cap);
    if (!buf)
        die_oom();
    for (;;) {
        if (len == cap) {
            cap *= 2;
            char *nb = realloc(buf, cap);
            if (!nb)
                die_oom();
            buf = nb;
        }
        size_t got = fread(buf + len, 1, cap - len, stdin);
        len += got;
        if (got == 0)
            break;
    }
    if (len == cap) {
        char *nb = realloc(buf, cap + 1);
        if (!nb)
            die_oom();
        buf = nb;
    }
    buf[len] = '\0';
    *len_out = len;
    return buf;
}

static void fail(const char *message)
{
    Buf b = {0};
    buf_puts(&b, "{\"ok\":false,\"error\":");
    buf_json(&b, message);
    buf_puts(&b, "}");
    fwrite(b.p, 1, b.len, stdout);
    exit(0);
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fputs("usage: emitter <c|cpp> <filename> [clang args...]\n", stderr);
        return 2;
    }
    int cpp = strcmp(argv[1], "cpp") == 0;
    if (!cpp && strcmp(argv[1], "c") != 0) {
        fputs("first argument must be 'c' or 'cpp'\n", stderr);
        return 2;
    }
    const char *filename = argv[2];

    const char *problem = ouro_load_libclang();
    if (problem) {
        /* A toolchain fault, not a source fault: say so on stderr and exit
         * non-zero so the caller reports it as what it is. */
        fprintf(stderr, "%s\n", problem);
        return 3;
    }

    size_t src_len = 0;
    char *src = read_stdin(&src_len);

    struct CXUnsavedFile unsaved;
    unsaved.Filename = filename;
    unsaved.Contents = src;
    unsaved.Length = (unsigned long)src_len;

    CXIndex index = clang_createIndex(0, 0);
    CXTranslationUnit tu = NULL;
    int rc = clang_parseTranslationUnit2(index, filename,
                                         (const char *const *)(argv + 3), argc - 3,
                                         &unsaved, 1, 0, &tu);
    if (rc != CXError_Success || !tu) {
        char msg[128];
        snprintf(msg, sizeof msg, "libclang could not create a translation unit (code %d)", rc);
        fail(msg);
    }

    Buf out = {0};
    buf_puts(&out, "{\"ok\":true,\"errorCount\":");

    unsigned ndiag = clang_getNumDiagnostics(tu);
    unsigned nerrors = 0;
    Buf messages = {0};
    unsigned shown = 0;
    for (unsigned i = 0; i < ndiag; i++) {
        CXDiagnostic d = clang_getDiagnostic(tu, i);
        if (clang_getDiagnosticSeverity(d) >= CXDiagnostic_Error) {
            nerrors++;
            if (shown < MAX_ERROR_MESSAGES) {
                if (shown++)
                    buf_putc(&messages, ',');
                buf_json_cx(&messages, clang_getDiagnosticSpelling(d));
            }
        }
        clang_disposeDiagnostic(d);
    }
    buf_putu(&out, nerrors);
    buf_puts(&out, ",\"errors\":[");
    if (messages.p)
        buf_putn(&out, messages.p, messages.len);
    buf_puts(&out, "],\"functions\":[");

    Ctx ctx = {.out = &out, .tu = tu, .main_file = filename, .cpp = cpp, .emitted = 0};
    clang_visitChildren(clang_getTranslationUnitCursor(tu), visit_all, &ctx);
    buf_puts(&out, "]}");

    fwrite(out.p, 1, out.len, stdout);
    fflush(stdout);

    /* The process is about to end; skipping the teardown of a 30 MB index
     * saves measurable wall time on every wrap. */
    _Exit(0);
}
