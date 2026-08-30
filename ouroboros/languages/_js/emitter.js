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
//
// Offsets are CODE POINT offsets, not the UTF-16 indices babel reports. Python
// indexes str by code point, so handing it a UTF-16 index splices in the wrong
// place in any file containing a character outside the basic plane — one emoji
// in a comment above a function is enough to corrupt every later edit and
// produce a file that no longer parses.

const parser = require("@babel/parser");

// cpBefore[i] = how many code points precede UTF-16 index i. Built once per
// parse; a per-offset conversion would be quadratic on a large file.
let cpBefore = null;

function buildCodePointTable(src) {
  cpBefore = new Int32Array(src.length + 1);
  let points = 0;
  for (let i = 0; i < src.length; i++) {
    cpBefore[i] = points;
    const code = src.charCodeAt(i);
    // A surrogate pair is two UTF-16 units and one code point: count the pair
    // once, at its first unit.
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < src.length) {
      const next = src.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        i++;
        cpBefore[i] = points;
      }
    }
    points++;
  }
  cpBefore[src.length] = points;
}

function cp(utf16Index) {
  if (utf16Index === null || utf16Index === undefined) return utf16Index;
  const i = Math.max(0, Math.min(utf16Index, cpBefore.length - 1));
  return cpBefore[i];
}

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

// End offset of a directive prologue ("use strict" and friends), or `fallback`
// when there is none. Directives must stay the leading statements of their
// block, so nothing may be spliced above them.
function prologueEnd(directives, fallback) {
  let end = fallback;
  for (const d of directives || []) end = Math.max(end, d.end);
  return end;
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
    buildCodePointTable(src);
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
          // After the body's own directive prologue, not just after "{". A
          // function-level "use strict" only counts while it is still the first
          // statement; pushing code above it silently drops the function out of
          // strict mode (no error, different semantics).
          entry.hasDirectives = (body.directives || []).length > 0;
          entry.bodyStart = cp(prologueEnd(body.directives, body.start + 1));
          entry.bodyEnd = cp(body.end - 1); // the "}" position
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
        const ret = { start: cp(node.start), keywordEnd: cp(node.start + 6) };
        if (node.argument) {
          ret.argStart = cp(node.argument.start);
          ret.argEnd = cp(node.argument.end);
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
    // Where a file-level header may be spliced: below the `#!` line (the kernel
    // reads it only from byte 0) and below the file's own directive prologue.
    const interp = ast.program.interpreter;
    const headerStart = cp(prologueEnd(ast.program.directives, interp ? interp.end : 0));
    process.stdout.write(
      JSON.stringify({ ok: true, sourceType: ast.program.sourceType, headerStart, functions })
    );
  });
}

main();
