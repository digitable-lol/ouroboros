"""Стоимость заказа: позиции, оптовая скидка, налог, доставка."""

import sys

RATES = {"книги": 10, "еда": 5, "техника": 20}


def parse_item(raw):
    name, kind, qty, price = raw.split(":")
    return {"name": name, "kind": kind, "qty": int(qty), "price": int(price)}


def line_total(item):
    return item["qty"] * item["price"]


def bulk_discount(total, qty):
    if qty >= 10:
        return total * 90 // 100
    if qty >= 5:
        return total * 95 // 100
    return total


def tax(total, kind):
    rate = RATES.get(kind)
    if rate is None:
        raise KeyError("неизвестный разряд: " + kind)
    return total * rate // 100


def shipping(units):
    if units == 0:
        return 0
    if units > 30:
        return 0
    return 150 + 10 * units


def loyalty_bonus(total):
    return total // 100


def order_total(raws):
    units = 0
    money = 0
    for raw in raws:
        item = parse_item(raw)
        if item["qty"] <= 0:
            continue
        base = line_total(item)
        base = bulk_discount(base, item["qty"])
        money += base + tax(base, item["kind"])
        units += item["qty"]
    return money + shipping(units)


def main(argv):
    raws = argv[1:]
    try:
        print("итог", order_total(raws))
    except KeyError as err:
        print("отказ", err)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
