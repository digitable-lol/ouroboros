import sys
from collections import defaultdict

RESOURCES = {
    "storage":   {"GB": 1, "TB": 1000, "MB": 0.001},
    "compute":   {"hour": 1, "minute": 1/60},
    "bandwidth": {"GB": 1, "TB": 1000, "MB": 0.001},
}

RATES = {"storage": 0.02, "compute": 0.05, "bandwidth": 0.01}


def parse_log(path):
    totals = defaultdict(float)
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) != 4:
                continue
            account, resource, qty_str, unit = parts
            if resource not in RESOURCES:
                continue
            try:
                qty = float(qty_str)
            except ValueError:
                continue
            if qty < 0:
                continue
            factors = RESOURCES[resource]
            if unit not in factors:
                continue
            cost = qty * factors[unit] * RATES[resource]
            totals[account] += cost
    return totals


def apply_discount(pre_total):
    if pre_total > 50:
        return 50 + (pre_total - 50) * 0.9
    return pre_total


def main():
    path = sys.argv[1]
    totals = parse_log(path)
    rows = []
    for account, pre_total in totals.items():
        final = apply_discount(pre_total)
        rows.append((account, f"{final:.2f}"))
    rows.sort(key=lambda r: (-float(r[1]), r[0]))
    print("account,bill")
    for account, bill in rows:
        print(f"{account},{bill}")


if __name__ == "__main__":
    main()
