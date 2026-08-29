const N = Number(process.argv[2] || 20000);

function add(a, b) {
  return a + b;
}

function div(a, b) {
  if (b === 0) throw new RangeError("деление на ноль");
  return a / b;
}

function main() {
  let total = 0;
  for (let i = 0; i < N; i++) {
    total = add(total, i);
  }
  try {
    div(1, 0);
  } catch (e) {
    // проглочено намеренно
  }
  return total;
}

console.log(main());
