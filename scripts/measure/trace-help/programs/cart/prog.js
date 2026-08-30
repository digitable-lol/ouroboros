"use strict";
// Корзина: сложение позиций, купон, доставка.

const COUPONS = { LETO: 15, ZIMA: 5 };

function parseLine(raw) {
  const parts = raw.split(":");
  return { name: parts[0], qty: Number(parts[1]), unit: Number(parts[2]) };
}

function priceOf(line) {
  return line.qty * line.unit;
}

function couponCut(total, code) {
  const pct = COUPONS[code];
  if (pct === undefined) throw new Error("нет такого купона: " + code);
  return Math.floor((total * pct) / 100);
}

function freeShipping(total) {
  return total >= 5000;
}

function shippingCost(total) {
  return freeShipping(total) ? 0 : 300;
}

function giftFor(total) {
  return total > 100000 ? "кружка" : null;
}

function checkout(raws, code) {
  let total = 0;
  for (const raw of raws) {
    const line = parseLine(raw);
    if (line.qty === 0) continue;
    total += priceOf(line);
  }
  const cut = code === "-" ? 0 : couponCut(total, code);
  const net = total - cut;
  return net + shippingCost(net);
}

function main(argv) {
  const code = argv[0];
  console.log("к оплате", checkout(argv.slice(1), code));
  return 0;
}

main(process.argv.slice(2));
