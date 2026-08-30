"""Повторные попытки запроса по журналу кодов ответа."""

import sys


def classify(code):
    if code >= 500:
        return "сбой"
    if code == 429:
        return "перегруз"
    if code >= 400:
        return "отказ"
    return "ок"


def backoff(attempt):
    return 100 * (2 ** attempt)


def should_retry(kind):
    return kind in ("сбой", "перегруз")


def alert(code):
    return "тревога:" + str(code)


def attempt_once(codes, i):
    code = codes[i]
    return code, classify(code)


def run(codes, limit):
    waited = 0
    i = 0
    while i < len(codes):
        code, kind = attempt_once(codes, i)
        if not should_retry(kind):
            return {"код": code, "разряд": kind, "ждали": waited, "попыток": i + 1}
        if i + 1 >= limit:
            raise TimeoutError("исчерпаны попытки после " + str(i + 1))
        waited += backoff(i)
        i += 1
    return {"код": 0, "разряд": "пусто", "ждали": waited, "попыток": i}


def main(argv):
    limit = int(argv[1])
    codes = [int(x) for x in argv[2:]]
    try:
        print("исход", run(codes, limit))
    except TimeoutError as err:
        print("сдались:", err)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
