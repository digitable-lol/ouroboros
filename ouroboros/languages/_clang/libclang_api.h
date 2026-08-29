/* Minimal declaration of the libclang C API used by emitter.c.
 *
 * WHY THIS FILE EXISTS. The emitter needs libclang's headers to compile, but
 * the headers ship in a separate distribution package (llvm's -dev / -devel)
 * that the Python dependency `libclang` does not install -- that wheel carries
 * only the shared object. On a host with the wheel and no -dev package there
 * would be no way to build the emitter at all.
 *
 * So the build prefers the real <clang-c/Index.h> whenever it can find one
 * (emitter.c is compiled with -DOURO_SYSTEM_CLANG_C then) and falls back to
 * this file otherwise. The two are held to be interchangeable by a test that
 * builds the emitter BOTH ways and compares the JSON byte for byte
 * (tests/test_clangbridge.py::test_vendored_header_matches_system_header) --
 * the layouts below are not trusted, they are checked.
 *
 * Everything here is the stable C ABI of libclang: the struct layouts have not
 * changed since LLVM 3.x and the enum values below were read out of LLVM 21's
 * own header. Only what emitter.c calls is declared.
 */

#ifndef OUROBOROS_LIBCLANG_API_H
#define OUROBOROS_LIBCLANG_API_H

typedef void *CXIndex;
typedef struct CXTranslationUnitImpl *CXTranslationUnit;
typedef void *CXFile;
typedef void *CXDiagnostic;

typedef struct { const void *data; unsigned private_flags; } CXString;
typedef struct { const void *ptr_data[2]; unsigned int_data; } CXSourceLocation;
typedef struct {
  const void *ptr_data[2];
  unsigned begin_int_data;
  unsigned end_int_data;
} CXSourceRange;
typedef struct { int kind; int xdata; const void *data[3]; } CXCursor;
typedef struct { int kind; void *data[2]; } CXType;
typedef struct { unsigned int_data[4]; void *ptr_data; } CXToken;

struct CXUnsavedFile {
  const char *Filename;
  const char *Contents;
  unsigned long Length;
};

/* --- enum members used by emitter.c (values from LLVM 21's clang-c) ------- */
enum {
  CXCursor_StructDecl = 2,
  CXCursor_UnionDecl = 3,
  CXCursor_ClassDecl = 4,
  CXCursor_FunctionDecl = 8,
  CXCursor_CXXMethod = 21,
  CXCursor_Namespace = 22,
  CXCursor_ClassTemplate = 31,
  CXCursor_InitListExpr = 119,
  CXCursor_LambdaExpr = 144,
  CXCursor_CompoundStmt = 202,
  CXCursor_ReturnStmt = 214
};
enum {
  CXType_Void = 2, CXType_Bool = 3, CXType_Char_U = 4, CXType_UChar = 5,
  CXType_UShort = 8, CXType_UInt = 9, CXType_ULong = 10, CXType_ULongLong = 11,
  CXType_Char_S = 13, CXType_SChar = 14, CXType_WChar = 15, CXType_Short = 16,
  CXType_Int = 17, CXType_Long = 18, CXType_LongLong = 19,
  CXType_Float = 21, CXType_Double = 22, CXType_LongDouble = 23,
  CXType_Pointer = 101, CXType_Record = 105, CXType_Enum = 106
};
enum { CXDiagnostic_Error = 3 };
enum CXChildVisitResult {
  CXChildVisit_Break = 0,
  CXChildVisit_Continue = 1,
  CXChildVisit_Recurse = 2
};
enum { CXError_Success = 0 };

typedef void *CXClientData;
typedef enum CXChildVisitResult (*CXCursorVisitor)(CXCursor cursor,
                                                   CXCursor parent,
                                                   CXClientData client_data);

/* --- functions -----------------------------------------------------------
 *
 * Resolved with dlopen/dlsym rather than linked, and the pointers are named
 * exactly like the API so the emitter's own code reads as ordinary libclang
 * calls. Linking would have been shorter but does not work everywhere: the
 * copy of libclang inside the `libclang` Python wheel is installed as plain
 * `libclang.so` while its recorded SONAME is `libclang.so.18.1`, so a binary
 * linked against that file asks the loader at startup for a name that exists
 * nowhere on disk and dies before main(). Opening the file by path sidesteps
 * the whole question, and lets ONE built emitter work with any libclang.
 */

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

#define OURO_CLANG_FUNCS(F)                                                    \
  F(const char *, clang_getCString, (CXString))                                \
  F(void, clang_disposeString, (CXString))                                     \
  F(CXIndex, clang_createIndex, (int, int))                                    \
  F(int, clang_parseTranslationUnit2,                                          \
    (CXIndex, const char *, const char *const *, int, struct CXUnsavedFile *,  \
     unsigned, unsigned, CXTranslationUnit *))                                 \
  F(unsigned, clang_getNumDiagnostics, (CXTranslationUnit))                    \
  F(CXDiagnostic, clang_getDiagnostic, (CXTranslationUnit, unsigned))          \
  F(void, clang_disposeDiagnostic, (CXDiagnostic))                             \
  F(int, clang_getDiagnosticSeverity, (CXDiagnostic))                          \
  F(CXString, clang_getDiagnosticSpelling, (CXDiagnostic))                     \
  F(CXCursor, clang_getTranslationUnitCursor, (CXTranslationUnit))             \
  F(unsigned, clang_visitChildren, (CXCursor, CXCursorVisitor, CXClientData))  \
  F(int, clang_getCursorKind, (CXCursor))                                      \
  F(CXString, clang_getCursorSpelling, (CXCursor))                             \
  F(CXCursor, clang_getCursorSemanticParent, (CXCursor))                       \
  F(unsigned, clang_isCursorDefinition, (CXCursor))                            \
  F(CXSourceLocation, clang_getCursorLocation, (CXCursor))                     \
  F(CXSourceRange, clang_getCursorExtent, (CXCursor))                          \
  F(int, clang_Cursor_getNumArguments, (CXCursor))                             \
  F(CXCursor, clang_Cursor_getArgument, (CXCursor, unsigned))                  \
  F(CXType, clang_getCursorType, (CXCursor))                                   \
  F(CXType, clang_getCursorResultType, (CXCursor))                             \
  F(CXType, clang_getCanonicalType, (CXType))                                  \
  F(CXType, clang_getPointeeType, (CXType))                                    \
  F(CXString, clang_getTypeSpelling, (CXType))                                 \
  F(unsigned, clang_isConstQualifiedType, (CXType))                            \
  F(unsigned, clang_isVolatileQualifiedType, (CXType))                         \
  F(CXSourceLocation, clang_getRangeStart, (CXSourceRange))                    \
  F(CXSourceLocation, clang_getRangeEnd, (CXSourceRange))                      \
  F(void, clang_getExpansionLocation,                                          \
    (CXSourceLocation, CXFile *, unsigned *, unsigned *, unsigned *))          \
  F(CXString, clang_getFileName, (CXFile))                                     \
  F(void, clang_tokenize, (CXTranslationUnit, CXSourceRange, CXToken **,       \
                           unsigned *))                                        \
  F(CXString, clang_getTokenSpelling, (CXTranslationUnit, CXToken))            \
  F(CXSourceLocation, clang_getTokenLocation, (CXTranslationUnit, CXToken))    \
  F(void, clang_disposeTokens, (CXTranslationUnit, CXToken *, unsigned))

#define OURO_DECLARE_PTR(ret, name, args) static ret(*name) args;
OURO_CLANG_FUNCS(OURO_DECLARE_PTR)
#undef OURO_DECLARE_PTR

/* Names tried when the caller does not say which libclang to use. */
static const char *const ouro_libclang_names[] = {
    "libclang.so", "libclang.so.1", "libclang-21.so.1", "libclang-20.so.1",
    "libclang-19.so.1", "libclang-18.so.1", "libclang.dylib", NULL};

/* Open libclang and bind every entry point. Returns NULL on success, or a
 * message describing what went wrong. */
static const char *ouro_load_libclang(void)
{
  static char problem[512];
  void *handle = NULL;
  const char *chosen = getenv("OUROBOROS_LIBCLANG");
  if (chosen && *chosen) {
    handle = dlopen(chosen, RTLD_LAZY | RTLD_LOCAL);
  } else {
    for (int i = 0; ouro_libclang_names[i] && !handle; i++)
      handle = dlopen(ouro_libclang_names[i], RTLD_LAZY | RTLD_LOCAL);
  }
  if (!handle) {
    snprintf(problem, sizeof problem, "cannot open libclang (%s): %s",
             chosen && *chosen ? chosen : "no OUROBOROS_LIBCLANG set",
             dlerror() ? dlerror() : "unknown error");
    return problem;
  }
#define OURO_BIND_PTR(ret, name, args)                                         \
  do {                                                                         \
    *(void **)(&name) = dlsym(handle, #name);                                  \
    if (!name) {                                                               \
      snprintf(problem, sizeof problem, "libclang is missing %s", #name);      \
      return problem;                                                          \
    }                                                                          \
  } while (0);
  OURO_CLANG_FUNCS(OURO_BIND_PTR)
#undef OURO_BIND_PTR
  return NULL;
}

#endif /* OUROBOROS_LIBCLANG_API_H */
