import sys
from collections import defaultdict

UNITS = {
    "storage":   {"GB": 1, "TB": 1000, "MB": 0.001},
    "compute":   {"hour": 1, "minute": 1/60},
    "bandwidth": {"GB": 1, "TB": 1000, "MB": 0.001},
}

RATES = {"storage": 0.02, "compute": 0.05, "bandwidth": 0.01}

def parse(path):
    totals = defaultdict(float)
    with open(path) as f:
        for line in f:
            tokens = line.split()
            if len(tokens) != 4:
                continue
            account, resource, qty_str, unit = tokens
            if resource not in UNITS:
                continue
            try:
                qty = float(qty_str)
            except ValueError:
                continue
            if qty < 0:
                continue
            factor = UNITS[resource].get(unit)
            if factor is None:
                continue
            totals[account] += qty * factor * RATES[resource]
    return totals

def bill(pre_total):
    if pre_total > 50:
        return 50 + (pre_total - 50) * 0.9
    return pre_total

def main():
    path = sys.argv[1]
    totals = parse(path)
    rows = [(account, f"{bill(pre):.2f}") for account, pre in totals.items()]
    rows.sort(key=lambda r: (-float(r[1]), r[0]))
    print("account,bill")
    for account, b in rows:
        print(f"{account},{b}")

if __name__ == "__main__":
    main()
