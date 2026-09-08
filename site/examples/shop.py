"""What one order costs: goods, the regular-customer discount, delivery."""

import sys

PRICES = {"tea": 18.00, "mug": 12.50, "kettle": 21.50}


def subtotal(order):
    return sum(PRICES[name] for name in order)


def discount(amount):
    """Regular customers get 10% off."""
    return round(amount * 0.10, 2)


def delivery(amount):
    """Delivery is free from 50.00."""
    return 0.00 if amount >= 50.00 else 5.00


def total(order):
    goods = subtotal(order)
    goods = goods - discount(goods)
    return round(goods + delivery(goods), 2)


if __name__ == "__main__":
    print(f"Total: {total(sys.argv[1:]):.2f}")
