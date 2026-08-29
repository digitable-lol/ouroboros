from ouroboros_runtime import log as _ouro_log
import sys

# Weight of each sensor in the final health score; unknown sensors weigh 1.0.
WEIGHTS = {"cpu": 2.0, "mem": 1.5, "disk": 1.0, "net": 0.5}


@_ouro_log
def parse(text):
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        rows.append((parts[1], parts[2]))
    return rows


@_ouro_log
def drop_invalid(rows):
    out = []
    for sensor, raw in rows:
        try:
            value = float(raw)
        except ValueError:
            continue
        if value < 0:
            continue
        out.append((sensor, value))
    return out


@_ouro_log
def dedupe_last(rows):
    latest = {}
    for sensor, value in rows:
        latest[sensor] = value
    return latest


@_ouro_log
def normalize(values):
    total = sum(values.values())
    return {sensor: value / total for sensor, value in values.items()}


@_ouro_log
def weighted_score(norm):
    score = 0.0
    for sensor, frac in norm.items():
        score += frac * WEIGHTS.get(sensor, 1.0)
    return score


@_ouro_log
def main(path):
    text = open(path).read()
    rows = parse(text)
    rows = drop_invalid(rows)
    values = dedupe_last(rows)
    norm = normalize(values)
    score = weighted_score(norm)
    print(f"{round(score * 100, 2):.2f}")


if __name__ == "__main__":
    main(sys.argv[1])
