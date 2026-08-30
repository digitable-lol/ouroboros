// Ouroboros Go range-emitter.
//
// Reads Go source on stdin, parses it with the standard library's own parser
// (go/parser + go/ast), and prints a JSON description of every function
// definition in the file: where its body's braces are, which parameters can be
// named in an expression, and where its result types sit. It performs NO code
// generation — the Python side splices text at these byte offsets. This is the
// same split the JavaScript backend has with _js/emitter.js and the C/C++ one
// with _clang/emitter.c: the parser lives in the language it parses, and what
// crosses the process boundary is numbers.
//
// Usage:  emitter <filename>      source on stdin
// Output on success: {"ok":true,"errorCount":0,"errors":[],"package":"main","functions":[...]}
// Output when stdin could not be read at all: {"ok":false,"error":"..."}
//
// All offsets are BYTE offsets into the buffer that was handed in (go/token
// counts bytes), so the Python side splices bytes and stays correct on source
// with non-ASCII identifiers, strings or comments.
//
// Two fields the C contract carries are deliberately absent here, because the
// Go instrumentation does not need them:
//
//   - no "extentStart": the C and JavaScript backends need an anchor for an
//     injected #include / import line. Go needs no import at all — the runtime
//     helper is a file in the SAME package, so the wrapped code refers to it by
//     plain package-scope names and nothing is spliced above the function.
//   - no "returns": C and JavaScript capture the result by rewriting every
//     return site. Go names the results in the signature instead and reads them
//     from a deferred closure, so return statements are never touched — which
//     is also why `return f()`, forwarding another call's several results,
//     needs no special case.
package main

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/scanner"
	"go/token"
	"io"
	"os"
)

// name is one identifier in a signature, with the byte range it occupies, so
// the Python side can replace a blank "_" in place without touching its
// neighbours.
type name struct {
	Text  string `json:"text"`
	Start int    `json:"start"`
	End   int    `json:"end"`
}

// param is one declared parameter VALUE (a field `a, b int` yields two).
// Usable is false when the value cannot be named in an expression — an
// unnamed parameter (`func f(int)`) or the blank identifier (`func f(_ int)`).
// Its value is genuinely unreachable, so the record shows a placeholder there
// rather than a wrong value.
type param struct {
	Name   string `json:"name"`
	Usable bool   `json:"usable"`
}

// resultField is one field of the result list: `(a, b int)` is one field with
// two names, `(int, error)` is two fields with none. TypeStart/TypeEnd bound
// the type expression's source text, which the Python side re-uses verbatim
// when it has to give an unnamed result a name.
type resultField struct {
	TypeStart int    `json:"typeStart"`
	TypeEnd   int    `json:"typeEnd"`
	Names     []name `json:"names"`
}

// results describes the whole result clause. Parenthesized is false for the
// single bare type in `func f() int`, which has to gain parentheses before a
// name can be written into it.
type results struct {
	Start         int           `json:"start"`
	End           int           `json:"end"`
	Parenthesized bool          `json:"parenthesized"`
	Fields        []resultField `json:"fields"`
}

type function struct {
	Name          string   `json:"name"`
	QualifiedName string   `json:"qualifiedName"`
	BodyStart     int      `json:"bodyStart"`
	BodyEnd       int      `json:"bodyEnd"`
	Params        []param  `json:"params"`
	Results       *results `json:"results"`
}

type answer struct {
	OK         bool       `json:"ok"`
	Error      string     `json:"error,omitempty"`
	ErrorCount int        `json:"errorCount"`
	Errors     []string   `json:"errors"`
	Package    string     `json:"package"`
	Functions  []function `json:"functions"`
}

// maxErrors bounds the reported diagnostic list. errorCount stays the true
// total; only the printed messages are cut, which is all any caller shows.
const maxErrors = 5

// receiverName renders a method's receiver the way the Go runtime itself names
// it in a stack trace, minus the package: `Calc` for a value receiver and
// `(*Calc)` for a pointer one. Type parameters are dropped (`Box[T]` -> `Box`),
// because the name identifies the method, not the instantiation.
func receiverName(expr ast.Expr) string {
	switch t := expr.(type) {
	case *ast.StarExpr:
		return "(*" + receiverName(t.X) + ")"
	case *ast.Ident:
		return t.Name
	case *ast.IndexExpr: // Box[T]
		return receiverName(t.X)
	case *ast.IndexListExpr: // Box[K, V]
		return receiverName(t.X)
	}
	return ""
}

func collectParams(list *ast.FieldList) []param {
	out := []param{}
	if list == nil {
		return out
	}
	for _, f := range list.List {
		if len(f.Names) == 0 {
			// `func f(int)` — the value exists but has no name to read it by.
			out = append(out, param{Name: "", Usable: false})
			continue
		}
		for _, n := range f.Names {
			out = append(out, param{Name: n.Name, Usable: n.Name != "_"})
		}
	}
	return out
}

func collectResults(fset *token.FileSet, list *ast.FieldList) *results {
	if list == nil || len(list.List) == 0 {
		return nil
	}
	r := &results{Parenthesized: list.Opening.IsValid(), Fields: []resultField{}}
	if r.Parenthesized {
		r.Start = fset.Position(list.Opening).Offset
		r.End = fset.Position(list.Closing).Offset + 1
	} else {
		r.Start = fset.Position(list.List[0].Type.Pos()).Offset
		r.End = fset.Position(list.List[0].Type.End()).Offset
	}
	for _, f := range list.List {
		field := resultField{
			TypeStart: fset.Position(f.Type.Pos()).Offset,
			TypeEnd:   fset.Position(f.Type.End()).Offset,
			Names:     []name{},
		}
		for _, n := range f.Names {
			field.Names = append(field.Names, name{
				Text:  n.Name,
				Start: fset.Position(n.Pos()).Offset,
				End:   fset.Position(n.End()).Offset,
			})
		}
		r.Fields = append(r.Fields, field)
	}
	return r
}

func emit(filename string, src []byte) answer {
	out := answer{OK: true, Errors: []string{}, Functions: []function{}}
	fset := token.NewFileSet()
	// AllErrors so the corruption gate sees the whole diagnostic list rather
	// than only the first — the same reason the C emitter passes
	// -ferror-limit=0. ParseComments keeps comment positions out of the way of
	// nothing in particular, but it also keeps the parser from re-associating
	// doc comments, which changes no offset we report.
	file, err := parser.ParseFile(fset, filename, src, parser.AllErrors|parser.ParseComments)
	if err != nil {
		var list scanner.ErrorList
		if e, isList := err.(scanner.ErrorList); isList {
			list = e
		}
		if len(list) == 0 {
			out.ErrorCount = 1
			out.Errors = append(out.Errors, err.Error())
			return out
		}
		out.ErrorCount = len(list)
		for i, e := range list {
			if i == maxErrors {
				break
			}
			out.Errors = append(out.Errors, e.Error())
		}
		return out
	}

	out.Package = file.Name.Name
	for _, decl := range file.Decls {
		fn, isFunc := decl.(*ast.FuncDecl)
		if !isFunc || fn.Body == nil {
			// A declaration with no body is implemented elsewhere (assembly, or
			// a //go:linkname target). There is nothing to instrument and no
			// brace to splice into.
			continue
		}
		qualified := fn.Name.Name
		if fn.Recv != nil && len(fn.Recv.List) == 1 {
			if recv := receiverName(fn.Recv.List[0].Type); recv != "" {
				qualified = recv + "." + fn.Name.Name
			}
		}
		out.Functions = append(out.Functions, function{
			Name:          fn.Name.Name,
			QualifiedName: qualified,
			BodyStart:     fset.Position(fn.Body.Lbrace).Offset,
			BodyEnd:       fset.Position(fn.Body.Rbrace).Offset,
			Params:        collectParams(fn.Type.Params),
			Results:       collectResults(fset, fn.Type.Results),
		})
	}
	return out
}

func main() {
	filename := "input.go"
	if len(os.Args) > 1 {
		filename = os.Args[1]
	}
	src, err := io.ReadAll(os.Stdin)
	if err != nil {
		body, _ := json.Marshal(answer{
			OK:     false,
			Error:  fmt.Sprintf("cannot read source on stdin: %v", err),
			Errors: []string{},
		})
		os.Stdout.Write(body)
		return
	}
	body, err := json.Marshal(emit(filename, src))
	if err != nil {
		// Marshalling our own struct cannot fail on well-formed UTF-8, but a
		// silent empty stdout would look to the caller like a crashed helper.
		fmt.Fprintf(os.Stderr, "cannot encode the answer: %v\n", err)
		os.Exit(1)
	}
	os.Stdout.Write(body)
}
