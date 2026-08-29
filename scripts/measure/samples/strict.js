"use strict";

function probe() {
  try {
    undeclared = 1;
    return "НЕ БРОСИЛО";
  } catch (e) {
    return "БРОСИЛО: " + e.constructor.name;
  }
}

console.log(probe());
