#!/usr/bin/env python3
"""Generate Design-B benchmark fixtures from one reference implementation.

The task is a multi-stage billing pipeline (parse → normalize units → rate →
aggregate per account → tiered discount → round → sort). The interesting bugs
are *wrong-value-propagation*: a wrong unit factor or a wrong discount rule
yields a plausible-but-wrong final number that only surfaces at the end, so
localizing it benefits from seeing each function's inputs/outputs.

This reference is GROUND TRUTH. It emits:
  * task/sample.log + task/sample_expected.csv   (given to the agent)
  * fixtures/hidden.log + fixtures/hidden_expected.csv  (held out; integrity)
"""

from __future__ import annotations

from pathlib import Path

# resource -> (rate per base unit, {unit: factor to base unit})
RESOURCES = {
    "storage": (0.02, {"GB": 1.0, "TB": 1000.0, "MB": 0.001}),  # base GB
    "compute": (0.05, {"hour": 1.0, "minute": 1.0 / 60.0}),      # base hour
    "bandwidth": (0.01, {"GB": 1.0, "TB": 1000.0, "MB": 0.001}), # base GB
}
DISCOUNT_THRESHOLD = 50.0
DISCOUNT_RATE = 0.10  # 10% off the amount ABOVE the threshold


def parse_line(line: str):
    parts = line.split()
    if len(parts) != 4:
        return None
    account, resource, qty, unit = parts
    if resource not in RESOURCES:
        return None
    rate, units = RESOURCES[resource]
    if unit not in units:
        return None
    try:
        q = float(qty)
    except ValueError:
        return None
    if q < 0:
        return None
    cost = q * units[unit] * rate
    return account, cost


def build_report(text: str) -> str:
    totals: dict[str, float] = {}
    for line in text.splitlines():
        r = parse_line(line)
        if r is None:
            continue
        account, cost = r
        totals[account] = totals.get(account, 0.0) + cost

    rows = []
    for account, pre in totals.items():
        if pre > DISCOUNT_THRESHOLD:
            final = DISCOUNT_THRESHOLD + (pre - DISCOUNT_THRESHOLD) * (1 - DISCOUNT_RATE)
        else:
            final = pre
        rows.append((account, final))

    rows.sort(key=lambda r: (-round(r[1], 2), r[0]))
    out = ["account,bill"]
    for account, final in rows:
        out.append(f"{account},{final:.2f}")
    return "\n".join(out) + "\n"


SAMPLE_LOG = """\
acme storage 1500 GB
acme bandwidth 2 TB
acme compute 720 minute
globex compute 100 hour
globex storage 500000 MB
globex bandwidth 3000 GB
initech storage 4 TB
hooli bandwidth 1234 GB
hooli storage 250 GB
# this line is a comment and must be skipped
acme storage 100
acme gpu 5 hour
globex storage 100 PB
initech compute -5 hour
hooli storage abc GB

"""

HIDDEN_LOG = """\
stark compute 1000 hour
stark bandwidth 10000 GB
umbrella storage 3000 GB
wayne bandwidth 5 TB
wayne storage 100 MB
oscorp bandwidth 777 GB
oscorp storage 11 GB
umbrella compute 0 minute
malformed line here too long to be valid
oscorp disk 5 GB
wayne compute 10 fortnight
stark storage -100 GB

"""


def main() -> None:
    root = Path(__file__).parent
    task = root / "task"
    fixtures = root / "fixtures"
    task.mkdir(exist_ok=True)
    fixtures.mkdir(exist_ok=True)

    (task / "sample.log").write_text(SAMPLE_LOG, encoding="utf-8")
    (task / "sample_expected.csv").write_text(build_report(SAMPLE_LOG), encoding="utf-8")
    (fixtures / "hidden.log").write_text(HIDDEN_LOG, encoding="utf-8")
    (fixtures / "hidden_expected.csv").write_text(build_report(HIDDEN_LOG), encoding="utf-8")

    print("sample_expected.csv:\n" + build_report(SAMPLE_LOG))
    print("hidden_expected.csv:\n" + build_report(HIDDEN_LOG))


if __name__ == "__main__":
    main()
