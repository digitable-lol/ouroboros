#!/usr/bin/env node
// Ouroboros JS/TS range-emitter.
//
// Reads source code on stdin, parses it with @babel/parser, and prints a JSON
// description of every block-bodied function and the `return` statements that
// belong to it (excluding returns nested in inner functions). It performs NO
// code generation — the Python side does the splicing. This keeps the parser
// integration a thin, Elixir-port-friendly helper.
//
// Usage:  node emitter.js <ext>     where <ext> is one of: js jsx ts tsx mjs cjs
// Output: {"ok": true, "sourceType": "...", "functions": [...]}  or {"ok": false, "error": "..."}

const parser = require("@babel/parser");

const FUNCTION_TYPES = new Set([
  "FunctionDeclaration",
  "FunctionExpression",
  "ArrowFunctionExpression",
  "ObjectMethod",
  "ClassMethod",
  "ClassPrivateMethod",
]);

function pluginsFor(ext) {
  switch (ext) {
    case "ts":
      return ["typescript"];
    case "tsx":
      return ["typescript", "jsx"];
    case "jsx":
      return ["jsx"];
    default: // js, mjs, cjs
      return ["jsx"];
  }
}

// Best-effort human name for a function node, using the parent for anonymous
// function expressions / arrows assigned to a variable or property.
function nameOf(node, parent) {
  if (node.id && node.id.name) return node.id.name;
  if (node.key) {
    if (node.key.name) return node.key.name;
    if (node.key.value !== undefined) return String(node.key.value);
  }
  if (parent) {
    if (parent.type === "VariableDeclarator" && parent.id && parent.id.name) {
      return parent.id.name;
    }
    if (
      (parent.type === "ObjectProperty" || parent.type === "ClassProperty" || parent.type === "PropertyDefinition") &&
      parent.key &&
      (parent.key.name || parent.key.value !== undefined)
    ) {
      return parent.key.name || String(parent.key.value);
    }
    if (parent.type === "AssignmentExpression" && parent.left && parent.left.property && parent.left.property.name) {
      return parent.left.property.name;
    }
  }
  return "anonymous";
}

// Referenceable parameter names (those we can safely evaluate at entry to log).
function paramNames(params) {
  const names = [];
  for (const p of params) {
    if (p.type === "Identifier") names.push(p.name);
    else if (p.type === "AssignmentPattern" && p.left.type === "Identifier") names.push(p.left.name);
    else if (p.type === "RestElement" && p.argument.type === "Identifier") names.push(p.argument.name);
    else if (p.type === "TSParameterProperty" && p.parameter && p.parameter.type === "Identifier")
      names.push(p.parameter.name);
    // destructuring patterns are skipped — not a single referenceable expression
  }
  return names;
}

function isNode(v) {
  return v && typeof v === "object" && typeof v.type === "string";
}

function main() {
  const ext = (process.argv[2] || "js").toLowerCase();
  let src = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (c) => (src += c));
  process.stdin.on("end", () => {
    let ast;
    try {
      ast = parser.parse(src, {
        sourceType: "unambiguous",
        plugins: pluginsFor(ext),
        ranges: false,
        errorRecovery: false,
      });
    } catch (e) {
      process.stdout.write(JSON.stringify({ ok: false, error: String(e.message || e) }));
      return;
    }

    const functions = [];

    // Recursive walk tracking the nearest enclosing function so each return is
    // attributed to the function it actually returns from.
    function walk(node, parent, enclosingFn) {
      if (FUNCTION_TYPES.has(node.type)) {
        const body = node.body;
        const isBlock = body && body.type === "BlockStatement";
        const entry = {
          name: nameOf(node, parent),
          type: node.type,
          isBlock: !!isBlock,
          isAsync: !!node.async,
          params: paramNames(node.params || []),
          returns: [],
        };
        if (isBlock) {
          entry.bodyStart = body.start + 1; // just after "{"
          entry.bodyEnd = body.end - 1; // the "}" position
        }
        functions.push(entry);
        // recurse with this function as the enclosing scope for returns
        for (const key of Object.keys(node)) {
          const child = node[key];
          if (key === "type" || key === "start" || key === "end" || key === "loc") continue;
          recurse(child, node, isBlock ? entry : enclosingFn);
        }
        return;
      }

      if (node.type === "ReturnStatement" && enclosingFn) {
        const ret = { start: node.start, keywordEnd: node.start + 6 };
        if (node.argument) {
          ret.argStart = node.argument.start;
          ret.argEnd = node.argument.end;
        } else {
          ret.argStart = null;
          ret.argEnd = null;
        }
        enclosingFn.returns.push(ret);
      }

      for (const key of Object.keys(node)) {
        if (key === "type" || key === "start" || key === "end" || key === "loc") continue;
        recurse(node[key], node, enclosingFn);
      }
    }

    function recurse(value, parent, enclosingFn) {
      if (Array.isArray(value)) {
        for (const item of value) recurse(item, parent, enclosingFn);
      } else if (isNode(value)) {
        walk(value, parent, enclosingFn);
      }
    }

    recurse(ast.program, null, null);
    process.stdout.write(JSON.stringify({ ok: true, sourceType: ast.program.sourceType, functions }));
  });
}

main();
