// Ouroboros C# range-emitter.
//
// Reads C# source on stdin, parses it with Roslyn — the compiler that already
// ships inside the .NET SDK (Roslyn/bincore/Microsoft.CodeAnalysis*.dll), no
// download and no NuGet package — and prints a JSON description of every member
// that has a body, plus the `return` statements belonging to each. It performs
// NO code generation: the Python side does the splicing. Same thin-helper shape
// as _java/Emitter.java and _js/emitter.js.
//
// Usage:  dotnet ouro_cs_emitter.dll   (source on stdin)
// Output: {"ok":true,"functions":[...]}  or  {"ok":false,"error":"..."}
//
// Offsets are CODE POINT offsets, not the UTF-16 indices Roslyn reports. Python
// indexes str by code point, so handing it a UTF-16 index would splice in the
// wrong place in any file holding a character outside the basic plane — one
// emoji in a comment above a method is enough to corrupt every later edit.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

internal static class Emitter
{
    /// <summary>The buffer handed to Roslyn, kept so offsets convert to code points.</summary>
    private static string _source = "";

    /// <summary>_codePoints[i] = how many code points precede UTF-16 index i.</summary>
    private static int[] _codePoints = Array.Empty<int>();

    /// <summary>
    /// <c>ref struct</c> type names. A ref struct cannot be boxed into the
    /// <c>object[]</c> snapshot and cannot be a generic type argument, so a member
    /// naming one in a parameter or in its return type is left alone rather than
    /// wrapped into code that will not compile.
    ///
    /// <para>Seeded with the ones the platform declares, then extended by
    /// <see cref="CollectRefStructs"/> with every <c>ref struct</c> declared in the
    /// file being wrapped. What is left over is a ref struct declared in a
    /// DIFFERENT file of the same project: this emitter reads syntax only, it never
    /// resolves a name to a declaration, so that one is invisible here and a member
    /// using it is wrapped into code that does not compile. That is the known hole,
    /// and it is written down in csharp_lang.py and in docs/limits.md rather than
    /// guessed around.</para>
    /// </summary>
    private static readonly HashSet<string> RefStructs = new()
    {
        "Span", "ReadOnlySpan", "ArgIterator", "TypedReference", "RuntimeArgumentHandle",
    };

    /// <summary>One `return` belonging to a member.</summary>
    private sealed class Ret
    {
        public int Start;
        public int KeywordEnd;
        public int StmtEnd;
        public int ArgStart = -1;   // -1 for a bare `return;`
        public int ArgEnd = -1;
        public bool IsRef;          // `return ref x;`
    }

    /// <summary>One member with a body.</summary>
    private sealed class Fn
    {
        public string Name = "";
        public string QualifiedName = "";
        public string Kind = "";
        public bool IsVoid;          // declaration carries no value out at all
        public string RetType;       // type argument for Ret&lt;T&gt;; null when unknown
        public List<string> Params = new();
        public int BodyStart = -1;   // just after `{`
        public int BodyEnd = -1;     // the `}`
        public int ArrowStart = -1;  // expression body: the `=>` token
        public int ExprStart = -1;
        public int ExprEnd = -1;
        public int TailEnd = -1;     // just past the `;` closing an expression body
        public bool ExprIsThrow;
        public bool HasYield;
        public List<Ret> Returns = new();
        public string Skip;          // non-null: why this member is left alone
    }

    private static readonly List<Fn> Functions = new();

    private static int Main()
    {
        using (var stdin = Console.OpenStandardInput())
        using (var buffer = new MemoryStream())
        {
            stdin.CopyTo(buffer);
            _source = new UTF8Encoding(false).GetString(buffer.ToArray());
        }
        BuildCodePointTable();

        SyntaxTree tree;
        try
        {
            tree = CSharpSyntaxTree.ParseText(
                _source,
                new CSharpParseOptions(LanguageVersion.Latest, DocumentationMode.None));
        }
        catch (Exception e)
        {
            Emit("{\"ok\":false,\"error\":" + Quote(e.Message) + "}");
            return 0;
        }

        foreach (Diagnostic d in tree.GetDiagnostics())
        {
            if (d.Severity != DiagnosticSeverity.Error)
            {
                continue;
            }
            int line = d.Location.GetLineSpan().StartLinePosition.Line + 1;
            Emit("{\"ok\":false,\"error\":"
                 + Quote(d.GetMessage() + " (line " + line + ")") + "}");
            return 0;
        }

        CollectRefStructs(tree.GetRoot());
        new Collector().Visit(tree.GetRoot());
        Emit(Render());
        return 0;
    }

    // ---- the walk ---------------------------------------------------------- //

    /// <summary>
    /// Collects members and attributes each `return` to the member it actually
    /// returns from. A `return` inside a lambda, an anonymous method or a local
    /// function returns from THAT body, not from the enclosing member, so those
    /// are walked with no enclosing member in scope — and are not wrapped
    /// themselves, exactly as the Java emitter treats lambdas and local classes.
    /// </summary>
    private sealed class Collector : CSharpSyntaxWalker
    {
        private readonly List<string> _types = new();
        private Fn _enclosing;

        public override void Visit(SyntaxNode node)
        {
            if (node == null)
            {
                return;
            }
            switch (node)
            {
                case BaseTypeDeclarationSyntax type:
                    // A nested type's members are named Outer.Inner.member, and a
                    // `return` inside one never leaves the enclosing member.
                    _types.Add(type.Identifier.ValueText);
                    InScope(null, () => base.Visit(node));
                    _types.RemoveAt(_types.Count - 1);
                    return;

                case LocalFunctionStatementSyntax:
                case AnonymousFunctionExpressionSyntax:
                    InScope(null, () => base.Visit(node));
                    return;

                case BaseMethodDeclarationSyntax method:
                    Member(node, MemberName(method), MemberKind(method),
                           method.ParameterList?.Parameters, method.Body,
                           method.ExpressionBody, ReturnTypeOf(method), IsVoidMember(method));
                    return;

                case PropertyDeclarationSyntax property when property.ExpressionBody != null:
                    Unwrappable(property.Identifier.ValueText, "expression-bodied-property");
                    base.Visit(node);
                    return;

                case IndexerDeclarationSyntax indexer when indexer.ExpressionBody != null:
                    Unwrappable("this[]", "expression-bodied-indexer");
                    base.Visit(node);
                    return;

                case AccessorDeclarationSyntax accessor:
                    Accessor(accessor);
                    return;

                case ReturnStatementSyntax ret:
                    Return(ret);
                    return;

                case YieldStatementSyntax:
                    // `yield` inside a `try` that has a `catch` is a compile error
                    // in C#, so an iterator cannot carry this wrap at all.
                    if (_enclosing != null)
                    {
                        _enclosing.HasYield = true;
                    }
                    base.Visit(node);
                    return;

                default:
                    base.Visit(node);
                    return;
            }
        }

        private void InScope(Fn scope, Action body)
        {
            Fn saved = _enclosing;
            _enclosing = scope;
            try
            {
                body();
            }
            finally
            {
                _enclosing = saved;
            }
        }

        /// <summary>
        /// Record a member this backend will not wrap, so the caller is told about
        /// it instead of quietly getting no records for it. `int P =&gt; e;` has no
        /// accessor body to splice into: turning it into one means writing
        /// `{ get { … } }`, which is reprinting the member's shape rather than
        /// splicing its body, and that is the one thing this design does not do.
        /// </summary>
        private void Unwrappable(string name, string reason)
        {
            var fn = new Fn
            {
                Name = name,
                QualifiedName = Qualify(name),
                Kind = "property",
                IsVoid = false,
                Skip = reason,
            };
            Functions.Add(fn);
        }

        private void Return(ReturnStatementSyntax node)
        {
            if (_enclosing != null)
            {
                var ret = new Ret
                {
                    Start = node.SpanStart,
                    KeywordEnd = node.ReturnKeyword.Span.End,
                    StmtEnd = node.Span.End,
                };
                ExpressionSyntax argument = node.Expression;
                if (argument != null)
                {
                    ret.ArgStart = argument.SpanStart;
                    ret.ArgEnd = argument.Span.End;
                    ret.IsRef = argument is RefExpressionSyntax;
                }
                _enclosing.Returns.Add(ret);
            }
            base.Visit(node);
        }

        private void Accessor(AccessorDeclarationSyntax node)
        {
            string keyword = node.Keyword.ValueText;
            SyntaxNode owner = node.Parent?.Parent;
            string ownerName;
            TypeSyntax ownerType;
            var parameters = new List<ParameterSyntax>();
            switch (owner)
            {
                case PropertyDeclarationSyntax property:
                    ownerName = property.Identifier.ValueText;
                    ownerType = property.Type;
                    break;
                case IndexerDeclarationSyntax indexer:
                    ownerName = "this[]";
                    ownerType = indexer.Type;
                    parameters.AddRange(indexer.ParameterList.Parameters);
                    break;
                case EventDeclarationSyntax ev:
                    ownerName = ev.Identifier.ValueText;
                    ownerType = null;
                    break;
                default:
                    base.Visit(node);
                    return;
            }

            bool isGetter = keyword == "get";
            // `set`, `init`, `add` and `remove` all take the implicit `value`, and
            // none of them carries a value out.
            bool takesValue = keyword is "set" or "init" or "add" or "remove";

            var fn = new Fn
            {
                Name = ownerName + "." + keyword,
                Kind = "accessor",
                IsVoid = !isGetter,
                RetType = isGetter && ownerType != null ? ownerType.ToString() : null,
            };
            fn.QualifiedName = Qualify(fn.Name);
            // The member's own type is both what a `get` carries out and what the
            // implicit `value` of a `set` holds: unusable either way when it is a
            // pointer or a ref struct.
            if (ownerType != null && MentionsPointer(ownerType))
            {
                fn.Skip = "pointer-type";
            }
            else if (ownerType != null && MentionsRefStruct(ownerType))
            {
                fn.Skip = "ref-struct-type";
            }
            foreach (ParameterSyntax p in parameters)
            {
                CheckParameter(fn, p);
            }
            if (takesValue)
            {
                fn.Params.Add("value");
            }
            Finish(fn, node, node.Body, node.ExpressionBody);
        }

        private void Member(SyntaxNode node, string name, string kind,
                            IEnumerable<ParameterSyntax> parameters, BlockSyntax body,
                            ArrowExpressionClauseSyntax arrow, TypeSyntax returnType,
                            bool isVoid)
        {
            var fn = new Fn
            {
                Name = name,
                Kind = kind,
                IsVoid = isVoid,
                RetType = isVoid ? null : CaptureType(node, returnType),
            };
            fn.QualifiedName = Qualify(name);
            if (returnType != null && MentionsPointer(returnType))
            {
                fn.Skip = "pointer-return";
            }
            else if (returnType is RefTypeSyntax)
            {
                // `ref int M()` — `return ref x;` cannot be routed through a helper
                // call, so the whole member is left alone.
                fn.Skip = "ref-return";
            }
            else if (returnType != null && MentionsRefStruct(returnType))
            {
                fn.Skip = "ref-struct-return";
            }
            if (parameters != null)
            {
                foreach (ParameterSyntax p in parameters)
                {
                    CheckParameter(fn, p);
                }
            }
            Finish(fn, node, body, arrow);
        }

        /// <summary>
        /// Decide whether one parameter may be read into the entry snapshot, and
        /// whether its very presence makes the member unwrappable.
        /// </summary>
        private static void CheckParameter(Fn fn, ParameterSyntax p)
        {
            bool isOut = false;
            foreach (SyntaxToken modifier in p.Modifiers)
            {
                if (modifier.IsKind(SyntaxKind.OutKeyword))
                {
                    isOut = true;
                }
            }
            if (p.Type != null && MentionsPointer(p.Type))
            {
                fn.Skip ??= "pointer-parameter";
                return;
            }
            if (p.Type != null && MentionsRefStruct(p.Type))
            {
                fn.Skip ??= "ref-struct-parameter";
                return;
            }
            if (p.Identifier.ValueText.Length == 0)
            {
                return;  // `__arglist`
            }
            if (isOut)
            {
                // An `out` parameter is not definitely assigned on entry; reading it
                // there is a compile error, so it is absent from `a`.
                return;
            }
            fn.Params.Add(p.Identifier.ValueText);
        }

        private void Finish(Fn fn, SyntaxNode node, BlockSyntax body,
                            ArrowExpressionClauseSyntax arrow)
        {
            if (body == null && arrow == null)
            {
                // abstract, extern, partial-without-body, or an auto-property
                // accessor: there is nothing to wrap.
                base.Visit(node);
                return;
            }
            if (body != null)
            {
                fn.BodyStart = body.SpanStart + 1;   // just past `{`
                fn.BodyEnd = body.Span.End - 1;      // the `}`
            }
            else
            {
                fn.ArrowStart = arrow.ArrowToken.SpanStart;
                fn.ExprStart = arrow.Expression.SpanStart;
                fn.ExprEnd = arrow.Expression.Span.End;
                fn.ExprIsThrow = arrow.Expression is ThrowExpressionSyntax;
                SyntaxToken semicolon = SemicolonOf(node);
                fn.TailEnd = semicolon.IsKind(SyntaxKind.None)
                    ? arrow.Span.End
                    : semicolon.Span.End;
            }

            InScope(fn, () => base.Visit(node));

            if (fn.HasYield)
            {
                fn.Skip ??= "iterator";
            }
            foreach (Ret ret in fn.Returns)
            {
                if (ret.IsRef)
                {
                    fn.Skip ??= "ref-return";
                }
            }
            Functions.Add(fn);
        }

        private string Qualify(string member)
        {
            var text = new StringBuilder();
            foreach (string t in _types)
            {
                text.Append(t).Append('.');
            }
            return text.Append(member).ToString();
        }
    }

    // ---- declaration shapes ------------------------------------------------ //

    private static string MemberName(BaseMethodDeclarationSyntax node)
    {
        switch (node)
        {
            case MethodDeclarationSyntax m:
                return m.Identifier.ValueText;
            case ConstructorDeclarationSyntax c:
                return c.Identifier.ValueText;
            case DestructorDeclarationSyntax d:
                return "~" + d.Identifier.ValueText;
            case OperatorDeclarationSyntax o:
                return "operator" + o.OperatorToken.ValueText;
            case ConversionOperatorDeclarationSyntax v:
                return "operator " + v.Type;
            default:
                return "?";
        }
    }

    private static string MemberKind(BaseMethodDeclarationSyntax node) => node switch
    {
        MethodDeclarationSyntax => "method",
        ConstructorDeclarationSyntax => "constructor",
        DestructorDeclarationSyntax => "destructor",
        _ => "operator",
    };

    private static TypeSyntax ReturnTypeOf(BaseMethodDeclarationSyntax node) => node switch
    {
        MethodDeclarationSyntax m => m.ReturnType,
        OperatorDeclarationSyntax o => o.ReturnType,
        ConversionOperatorDeclarationSyntax v => v.Type,
        _ => null,
    };

    /// <summary>True when the declaration carries no value out of the member.</summary>
    private static bool IsVoidMember(BaseMethodDeclarationSyntax node)
    {
        TypeSyntax returnType = ReturnTypeOf(node);
        if (returnType == null)
        {
            return true;   // constructor or destructor
        }
        if (returnType is PredefinedTypeSyntax predefined
            && predefined.Keyword.IsKind(SyntaxKind.VoidKeyword))
        {
            return true;
        }
        // `async Task M()` / `async ValueTask M()`: every `return` in it is bare.
        return IsAsync(node) && returnType is not GenericNameSyntax;
    }

    /// <summary>
    /// The type argument for <c>Ret&lt;T&gt;</c>: the type a `return` in this
    /// member actually carries. Naming it explicitly is what lets `return null;`,
    /// `return () =&gt; 1;` and `return [1, 2];` keep compiling — type inference
    /// on a bare generic helper cannot type any of them (CS0411).
    /// </summary>
    private static string CaptureType(SyntaxNode node, TypeSyntax returnType)
    {
        if (returnType == null)
        {
            return null;
        }
        if (node is BaseMethodDeclarationSyntax method && IsAsync(method))
        {
            // In an async method the `return` carries the awaited type, not the
            // declared `Task<T>`.
            return returnType is GenericNameSyntax generic
                   && generic.TypeArgumentList.Arguments.Count == 1
                ? generic.TypeArgumentList.Arguments[0].ToString()
                : null;
        }
        return returnType.ToString();
    }

    private static bool IsAsync(BaseMethodDeclarationSyntax node)
    {
        foreach (SyntaxToken modifier in node.Modifiers)
        {
            if (modifier.IsKind(SyntaxKind.AsyncKeyword))
            {
                return true;
            }
        }
        return false;
    }

    private static SyntaxToken SemicolonOf(SyntaxNode node) => node switch
    {
        MethodDeclarationSyntax m => m.SemicolonToken,
        ConstructorDeclarationSyntax c => c.SemicolonToken,
        DestructorDeclarationSyntax d => d.SemicolonToken,
        OperatorDeclarationSyntax o => o.SemicolonToken,
        ConversionOperatorDeclarationSyntax v => v.SemicolonToken,
        AccessorDeclarationSyntax a => a.SemicolonToken,
        _ => default,
    };

    private static bool MentionsPointer(TypeSyntax type)
    {
        foreach (SyntaxNode n in type.DescendantNodesAndSelf())
        {
            if (n is PointerTypeSyntax || n is FunctionPointerTypeSyntax)
            {
                return true;
            }
        }
        return false;
    }

    /// <summary>
    /// Adds every <c>ref struct</c> declared in this file to <see cref="RefStructs"/>.
    /// Cheap and exact for the common case — a helper struct declared beside the
    /// code that uses it — and it needs no name resolution.
    /// </summary>
    private static void CollectRefStructs(SyntaxNode root)
    {
        foreach (StructDeclarationSyntax decl in
                 root.DescendantNodesAndSelf().OfType<StructDeclarationSyntax>())
        {
            if (decl.Modifiers.Any(m => m.IsKind(SyntaxKind.RefKeyword)))
            {
                RefStructs.Add(decl.Identifier.ValueText);
            }
        }
    }

    private static bool MentionsRefStruct(TypeSyntax type)
    {
        foreach (SyntaxNode n in type.DescendantNodesAndSelf())
        {
            string name = n switch
            {
                GenericNameSyntax g => g.Identifier.ValueText,
                IdentifierNameSyntax i => i.Identifier.ValueText,
                _ => null,
            };
            if (name != null && RefStructs.Contains(name))
            {
                return true;
            }
        }
        return false;
    }

    // ---- output ------------------------------------------------------------ //

    private static string Render()
    {
        var out_ = new StringBuilder("{\"ok\":true,\"functions\":[");
        for (int i = 0; i < Functions.Count; i++)
        {
            Fn fn = Functions[i];
            if (i > 0)
            {
                out_.Append(',');
            }
            out_.Append("{\"name\":").Append(Quote(fn.Name))
                .Append(",\"qualifiedName\":").Append(Quote(fn.QualifiedName))
                .Append(",\"kind\":").Append(Quote(fn.Kind))
                .Append(",\"isVoid\":").Append(fn.IsVoid ? "true" : "false")
                .Append(",\"retType\":")
                .Append(fn.RetType == null ? "null" : Quote(fn.RetType))
                .Append(",\"skip\":").Append(fn.Skip == null ? "null" : Quote(fn.Skip))
                .Append(",\"bodyStart\":").Append(CodePoint(fn.BodyStart))
                .Append(",\"bodyEnd\":").Append(CodePoint(fn.BodyEnd))
                .Append(",\"arrowStart\":").Append(CodePoint(fn.ArrowStart))
                .Append(",\"exprStart\":").Append(CodePoint(fn.ExprStart))
                .Append(",\"exprEnd\":").Append(CodePoint(fn.ExprEnd))
                .Append(",\"tailEnd\":").Append(CodePoint(fn.TailEnd))
                .Append(",\"exprIsThrow\":").Append(fn.ExprIsThrow ? "true" : "false")
                .Append(",\"params\":[");
            for (int p = 0; p < fn.Params.Count; p++)
            {
                if (p > 0)
                {
                    out_.Append(',');
                }
                out_.Append(Quote(fn.Params[p]));
            }
            out_.Append("],\"returns\":[");
            for (int r = 0; r < fn.Returns.Count; r++)
            {
                Ret ret = fn.Returns[r];
                if (r > 0)
                {
                    out_.Append(',');
                }
                out_.Append("{\"start\":").Append(CodePoint(ret.Start))
                    .Append(",\"keywordEnd\":").Append(CodePoint(ret.KeywordEnd))
                    .Append(",\"stmtEnd\":").Append(CodePoint(ret.StmtEnd))
                    .Append(",\"argStart\":").Append(NullableCodePoint(ret.ArgStart))
                    .Append(",\"argEnd\":").Append(NullableCodePoint(ret.ArgEnd))
                    .Append('}');
            }
            out_.Append("]}");
        }
        return out_.Append("]}").ToString();
    }

    private static void BuildCodePointTable()
    {
        int n = _source.Length;
        _codePoints = new int[n + 1];
        int cp = 0;
        int i = 0;
        while (i < n)
        {
            _codePoints[i] = cp;
            if (char.IsHighSurrogate(_source[i]) && i + 1 < n && char.IsLowSurrogate(_source[i + 1]))
            {
                // A surrogate pair is ONE Python character; the low half maps to
                // the same code point index as the high half.
                _codePoints[i + 1] = cp;
                i += 2;
            }
            else
            {
                i += 1;
            }
            cp++;
        }
        _codePoints[n] = cp;
    }

    /// <summary>UTF-16 index as Roslyn reports it -> code point offset as Python counts it.</summary>
    private static int CodePoint(int utf16Index)
    {
        if (utf16Index < 0)
        {
            return -1;
        }
        int index = Math.Min(utf16Index, _source.Length);
        return _codePoints[index];
    }

    private static string NullableCodePoint(int utf16Index) =>
        utf16Index < 0 ? "null" : CodePoint(utf16Index).ToString(
            System.Globalization.CultureInfo.InvariantCulture);

    private static void Emit(string json)
    {
        byte[] bytes = new UTF8Encoding(false).GetBytes(json);
        using Stream stdout = Console.OpenStandardOutput();
        stdout.Write(bytes, 0, bytes.Length);
        stdout.Flush();
    }

    private static string Quote(string text)
    {
        var out_ = new StringBuilder(text.Length + 2).Append('"');
        foreach (char c in text)
        {
            switch (c)
            {
                case '"': out_.Append("\\\""); break;
                case '\\': out_.Append("\\\\"); break;
                case '\n': out_.Append("\\n"); break;
                case '\r': out_.Append("\\r"); break;
                case '\t': out_.Append("\\t"); break;
                default:
                    if (c < 0x20)
                    {
                        out_.Append("\\u").Append(((int)c).ToString("x4",
                            System.Globalization.CultureInfo.InvariantCulture));
                    }
                    else
                    {
                        out_.Append(c);
                    }
                    break;
            }
        }
        return out_.Append('"').ToString();
    }
}
