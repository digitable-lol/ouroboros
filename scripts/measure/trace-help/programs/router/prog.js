"use strict";
// Разбор пути запроса и выбор обработчика.

function splitPath(path) {
  return path.split("/").filter((s) => s.length > 0);
}

function isNumber(s) {
  return /^[0-9]+$/.test(s);
}

function matchUsers(parts) {
  if (parts[0] !== "users") return null;
  if (parts.length === 1) return { handler: "listUsers", id: null };
  if (isNumber(parts[1])) return { handler: "showUser", id: Number(parts[1]) };
  return { handler: "searchUsers", id: null };
}

function matchPosts(parts) {
  if (parts[0] !== "posts") return null;
  return { handler: "listPosts", id: null };
}

function notFound(path) {
  return { handler: "notFound", id: null };
}

function route(path) {
  const parts = splitPath(path);
  if (parts.length === 0) return { handler: "home", id: null };
  const byUsers = matchUsers(parts);
  if (byUsers) return byUsers;
  const byPosts = matchPosts(parts);
  if (byPosts) return byPosts;
  return notFound(path);
}

function main(paths) {
  for (const p of paths) {
    const r = route(p);
    console.log(p, "->", r.handler, r.id);
  }
  return paths.length;
}

main(process.argv.slice(2));
