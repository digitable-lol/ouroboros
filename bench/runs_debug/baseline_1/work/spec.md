# Task: fix the bug in pipeline.py

`pipeline.py` computes a single health score from a readings file:

```
python3 pipeline.py <file>
```

It currently prints the **wrong** score. `python3 pipeline.py sample.txt` prints
`320.00`, but the correct answer is in `sample_expected.txt` (`133.33`). Find and
fix the bug so the output matches. Do not change the intended behavior below or
hardcode the answer — your fix is also checked against a different hidden file.

## Intended behavior (the correct pipeline)

Input lines are `TIMESTAMP SENSOR VALUE`. Process in stages:

1. **parse** — keep lines with exactly 3 whitespace tokens as `(sensor, value)`.
2. **drop_invalid** — drop rows whose value is not a number or is negative.
3. **dedupe** — for each sensor keep its **last** value in file order.
4. **normalize** — divide each sensor's value by the **sum of all kept values**,
   so the normalized values sum to 1.
5. **weighted_score** — `sum(normalized[sensor] * weight[sensor])`, where weights
   are `cpu=2.0, mem=1.5, disk=1.0, net=0.5` and any other sensor weighs `1.0`.
6. **output** — print `score * 100` rounded to 2 decimals, as `f"{x:.2f}"`.

The bug is a single stage deviating from this spec. The final score is wrong but
the program does not crash, so the wrong number alone does not tell you which
stage is at fault.
