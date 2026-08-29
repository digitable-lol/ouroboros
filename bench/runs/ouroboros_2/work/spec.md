# Task: usage → billing report

Build a Python program `report.py` that reads a usage log and prints a per-account
billing report as CSV to **stdout**.

## Invocation

```
python3 report.py <logfile>
```

## Input format

Each line has exactly 4 whitespace-separated tokens:

```
ACCOUNT RESOURCE QUANTITY UNIT
```

A line is **valid** iff all of the following hold; every other line (wrong token
count, comments, unknown resource/unit, non-numeric or negative quantity, blank)
is **silently skipped**:

- `RESOURCE` is one of `storage`, `compute`, `bandwidth`.
- `QUANTITY` parses as a non-negative number (integer or decimal).
- `UNIT` is allowed for that resource (see below).

## Normalization, rates, cost

Each resource has a **base unit** and a **rate** (USD per base unit). Convert the
quantity to the base unit, then multiply by the rate:

| Resource   | Base unit | Allowed units (→ base factor)        | Rate (USD/base) |
|------------|-----------|--------------------------------------|-----------------|
| storage    | GB        | GB ×1, TB ×1000, MB ×0.001           | 0.02            |
| compute    | hour      | hour ×1, minute ×(1/60)              | 0.05            |
| bandwidth  | GB        | GB ×1, TB ×1000, MB ×0.001           | 0.01            |

`event_cost = quantity × unit_factor × rate`.

## Aggregation, discount, rounding

- For each account, `pre_total` = sum of its event costs.
- **Tiered discount:** if `pre_total > 50`, the first \$50 is charged in full and
  the amount **above** \$50 gets a 10% discount:
  `final = 50 + (pre_total − 50) × 0.9`. Otherwise `final = pre_total`.
  (The discount applies only to the excess, not the whole total. The threshold is
  strict: a `pre_total` of exactly 50 gets no discount.)
- Do **not** round intermediate values. Round only the final amount, printed with
  exactly two decimals via Python `f"{final:.2f}"`.

## Output

CSV to stdout:

```
account,bill
<account>,<bill>
...
```

Sorted by `bill` (the two-decimal amount) **descending**, then by `account`
ascending. The output ends with a trailing newline.

## Acceptance

`python3 report.py sample.log` must reproduce `sample_expected.csv` byte-for-byte.
Run it to check your work. Implement the real pipeline — do not hardcode the
expected output; `report.py` will also be validated against a different hidden
log of the same format.
