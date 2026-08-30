// Ouroboros Java range-emitter.
//
// Reads Java source on stdin, parses it with the compiler that ships inside the
// JDK (javax.tools + com.sun.source, no third-party library), and prints a JSON
// description of every method and constructor that has a body, plus the `return`
// statements belonging to each. It performs NO code generation — the Python side
// does the splicing. Same thin-helper shape as _js/emitter.js and _elixir/emit.exs.
//
// Usage:  java -cp <dir> Emitter
// Output: {"ok":true,"functions":[...]}  or  {"ok":false,"error":"..."}
//
// Offsets are CODE POINT offsets, not the UTF-16 char indices javac reports.
// Python indexes str by code point, so handing it a UTF-16 index would splice in
// the wrong place in any file containing a character outside the basic plane —
// one emoji in a comment above a method is enough to corrupt every later edit.

import com.sun.source.tree.BlockTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ExpressionStatementTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.LambdaExpressionTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.NewClassTree;
import com.sun.source.tree.ReturnTree;
import com.sun.source.tree.StatementTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreeScanner;
import com.sun.source.util.Trees;

import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.SimpleJavaFileObject;
import javax.tools.ToolProvider;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

public final class Emitter {

    /** The buffer handed to javac, kept so offsets can be converted to code points. */
    private static String source = "";

    private static final class InMemorySource extends SimpleJavaFileObject {
        private final String code;

        InMemorySource(String code) {
            super(URI.create("string:///Ouroboros.java"), Kind.SOURCE);
            this.code = code;
        }

        @Override
        public CharSequence getCharContent(boolean ignoreEncodingErrors) {
            return code;
        }
    }

    /** One `return` belonging to a method. */
    private static final class Ret {
        long start;
        long keywordEnd;
        long stmtEnd;
        Long argStart;   // null for a bare `return;`
        Long argEnd;
    }

    /** One method or constructor with a body. */
    private static final class Fn {
        String name;
        String qualifiedName;
        String kind;         // "method" | "constructor"
        boolean isVoid;
        String returnType;   // source spelling; "" when isVoid
        boolean returnIsPrimitive;
        List<String> params = new ArrayList<>();
        long bodyStart;      // just after `{`, or after an explicit super()/this() call
        long bodyEnd;        // the `}` position
        List<Ret> returns = new ArrayList<>();
    }

    private static final List<Fn> FUNCTIONS = new ArrayList<>();

    /**
     * The eight primitive type names. A primitive return type needs a value in
     * its declaration (a reference one takes {@code null}), and three of them —
     * {@code byte}, {@code short}, {@code char} — accept a constant that a
     * generic helper's inferred {@code Integer} cannot be narrowed to.
     */
    private static final List<String> PRIMITIVES = List.of(
            "byte", "short", "int", "long", "float", "double", "char", "boolean");

    public static void main(String[] args) throws IOException {
        source = new String(System.in.readAllBytes(), StandardCharsets.UTF_8);
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            System.out.print("{\"ok\":false,\"error\":"
                    + quote("this JDK exposes no system java compiler") + "}");
            return;
        }
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        Iterable<? extends CompilationUnitTree> units;
        JavacTask task;
        try {
            task = (JavacTask) compiler.getTask(null, null, diagnostics,
                    List.of("-proc:none"), null, List.of(new InMemorySource(source)));
            units = task.parse();
        } catch (RuntimeException e) {
            System.out.print("{\"ok\":false,\"error\":" + quote(String.valueOf(e.getMessage())) + "}");
            return;
        }
        for (Diagnostic<? extends JavaFileObject> d : diagnostics.getDiagnostics()) {
            if (d.getKind() == Diagnostic.Kind.ERROR) {
                System.out.print("{\"ok\":false,\"error\":"
                        + quote(d.getMessage(null) + " (line " + d.getLineNumber() + ")") + "}");
                return;
            }
        }

        SourcePositions positions = Trees.instance(task).getSourcePositions();
        for (CompilationUnitTree unit : units) {
            new Collector(unit, positions).scan(unit, null);
        }
        System.out.print(render());
    }

    /**
     * Walks the parse tree collecting methods and attributing each `return` to the
     * method it actually returns from. A `return` inside a lambda, an anonymous
     * class or a local class returns from THAT body, not from the enclosing
     * method, so those are scanned with no enclosing method in scope.
     */
    private static final class Collector extends TreeScanner<Void, Void> {
        private final CompilationUnitTree unit;
        private final SourcePositions positions;
        private final Deque<String> typeNames = new ArrayDeque<>();
        private Fn enclosing;

        Collector(CompilationUnitTree unit, SourcePositions positions) {
            this.unit = unit;
            this.positions = positions;
        }

        private long at(Tree t) {
            return positions.getStartPosition(unit, t);
        }

        private long end(Tree t) {
            return positions.getEndPosition(unit, t);
        }

        @Override
        public Void visitClass(ClassTree node, Void unused) {
            String simple = node.getSimpleName().toString();
            // An anonymous class has an empty name; nothing inside it belongs to
            // the enclosing method's call record.
            typeNames.addLast(simple.isEmpty() ? "$anon" : simple);
            Fn saved = enclosing;
            enclosing = null;
            try {
                return super.visitClass(node, unused);
            } finally {
                enclosing = saved;
                typeNames.removeLast();
            }
        }

        @Override
        public Void visitLambdaExpression(LambdaExpressionTree node, Void unused) {
            Fn saved = enclosing;
            enclosing = null;
            try {
                return super.visitLambdaExpression(node, unused);
            } finally {
                enclosing = saved;
            }
        }

        @Override
        public Void visitMethod(MethodTree node, Void unused) {
            BlockTree body = node.getBody();
            if (body == null) {
                // abstract, native or an interface method without a default body
                return super.visitMethod(node, unused);
            }
            Fn fn = new Fn();
            String simple = node.getName().toString();
            boolean isConstructor = "<init>".equals(simple);
            fn.name = isConstructor ? enclosingTypeName() : simple;
            fn.kind = isConstructor ? "constructor" : "method";
            fn.qualifiedName = qualify(fn.name);
            // A constructor completes like a void method: it has no value to
            // capture, so it takes the void shape.
            String returnSpelling = isConstructor || node.getReturnType() == null
                    ? "void" : node.getReturnType().toString();
            fn.isVoid = "void".equals(returnSpelling);
            fn.returnType = fn.isVoid ? "" : returnSpelling;
            fn.returnIsPrimitive = PRIMITIVES.contains(returnSpelling);
            for (VariableTree p : node.getParameters()) {
                fn.params.add(p.getName().toString());
            }
            // The body opens at `{`; splice AFTER it. For a constructor whose first
            // statement is an explicit super()/this() call, splice after THAT
            // instead: the JLS requires it to stay the first statement, and moving
            // anything above it does not compile.
            fn.bodyStart = at(body) + 1;
            long afterCtorCall = explicitConstructorInvocationEnd(body);
            if (afterCtorCall >= 0) {
                fn.bodyStart = afterCtorCall;
            }
            fn.bodyEnd = end(body) - 1;

            Fn saved = enclosing;
            enclosing = fn;
            try {
                scan(body, null);
            } finally {
                enclosing = saved;
            }
            FUNCTIONS.add(fn);
            return null;
        }

        /**
         * End offset of a leading {@code super(...)} / {@code this(...)} statement,
         * or -1 when the body does not start with one.
         */
        private long explicitConstructorInvocationEnd(BlockTree body) {
            List<? extends StatementTree> statements = body.getStatements();
            if (statements.isEmpty()) {
                return -1;
            }
            StatementTree first = statements.get(0);
            if (!(first instanceof ExpressionStatementTree stmt)) {
                return -1;
            }
            ExpressionTree expression = stmt.getExpression();
            if (!(expression instanceof MethodInvocationTree call)) {
                return -1;
            }
            String target = call.getMethodSelect().toString();
            if (target.equals("super") || target.equals("this")
                    || target.endsWith(".super") || target.endsWith(".this")) {
                return end(first);
            }
            return -1;
        }

        @Override
        public Void visitNewClass(NewClassTree node, Void unused) {
            // The arguments are evaluated in the enclosing method; only the class
            // body (if any) is a different scope, and visitClass handles that.
            return super.visitNewClass(node, unused);
        }

        @Override
        public Void visitReturn(ReturnTree node, Void unused) {
            if (enclosing != null) {
                Ret ret = new Ret();
                ret.start = at(node);
                ret.keywordEnd = ret.start + "return".length();
                ret.stmtEnd = end(node);
                ExpressionTree argument = node.getExpression();
                if (argument != null) {
                    ret.argStart = at(argument);
                    ret.argEnd = end(argument);
                }
                enclosing.returns.add(ret);
            }
            return super.visitReturn(node, unused);
        }

        private String enclosingTypeName() {
            return typeNames.isEmpty() ? "" : typeNames.peekLast();
        }

        private String qualify(String memberName) {
            StringBuilder out = new StringBuilder();
            for (String t : typeNames) {
                out.append(t).append('.');
            }
            return out.append(memberName).toString();
        }
    }

    // ---- output ---------------------------------------------------------- //

    private static String render() {
        StringBuilder out = new StringBuilder("{\"ok\":true,\"functions\":[");
        for (int i = 0; i < FUNCTIONS.size(); i++) {
            Fn fn = FUNCTIONS.get(i);
            if (i > 0) {
                out.append(',');
            }
            out.append("{\"name\":").append(quote(fn.name))
               .append(",\"qualifiedName\":").append(quote(fn.qualifiedName))
               .append(",\"kind\":").append(quote(fn.kind))
               .append(",\"isVoid\":").append(fn.isVoid)
               .append(",\"returnType\":").append(quote(fn.returnType))
               .append(",\"returnIsPrimitive\":").append(fn.returnIsPrimitive)
               .append(",\"bodyStart\":").append(codePoint(fn.bodyStart))
               .append(",\"bodyEnd\":").append(codePoint(fn.bodyEnd))
               .append(",\"params\":[");
            for (int p = 0; p < fn.params.size(); p++) {
                if (p > 0) {
                    out.append(',');
                }
                out.append(quote(fn.params.get(p)));
            }
            out.append("],\"returns\":[");
            for (int r = 0; r < fn.returns.size(); r++) {
                Ret ret = fn.returns.get(r);
                if (r > 0) {
                    out.append(',');
                }
                out.append("{\"start\":").append(codePoint(ret.start))
                   .append(",\"keywordEnd\":").append(codePoint(ret.keywordEnd))
                   .append(",\"stmtEnd\":").append(codePoint(ret.stmtEnd))
                   .append(",\"argStart\":").append(ret.argStart == null
                           ? "null" : String.valueOf(codePoint(ret.argStart)))
                   .append(",\"argEnd\":").append(ret.argEnd == null
                           ? "null" : String.valueOf(codePoint(ret.argEnd)))
                   .append('}');
            }
            out.append("]}");
        }
        return out.append("]}").toString();
    }

    /** UTF-16 index as javac reports it -> code point offset as Python counts it. */
    private static long codePoint(long utf16Index) {
        int index = (int) Math.max(0, Math.min(utf16Index, source.length()));
        return source.codePointCount(0, index);
    }

    private static String quote(String text) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    private Emitter() {
    }
}
