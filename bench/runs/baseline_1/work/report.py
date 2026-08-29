import sys
from collections import defaultdict

RESOURCES = {
    'storage':   {'units': {'GB': 1, 'TB': 1000, 'MB': 0.001}, 'rate': 0.02},
    'compute':   {'units': {'hour': 1, 'minute': 1/60},         'rate': 0.05},
    'bandwidth': {'units': {'GB': 1, 'TB': 1000, 'MB': 0.001}, 'rate': 0.01},
}

def main():
    logfile = sys.argv[1]
    totals = defaultdict(float)

    with open(logfile) as f:
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
            res = RESOURCES[resource]
            if unit not in res['units']:
                continue
            totals[account] += qty * res['units'][unit] * res['rate']

    rows = []
    for account, pre_total in totals.items():
        if pre_total > 50:
            final = 50 + (pre_total - 50) * 0.9
        else:
            final = pre_total
        rows.append((account, f"{final:.2f}"))

    rows.sort(key=lambda r: (-float(r[1]), r[0]))

    print("account,bill")
    for account, bill in rows:
        print(f"{account},{bill}")

if __name__ == '__main__':
    main()
