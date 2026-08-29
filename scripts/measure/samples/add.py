"""Пример для замера: сложение, деление и накопление."""
import sys

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000


def add(a, b):
    return a + b


def div(a, b):
    return a / b


def main():
    total = 0
    for i in range(N):
        total = add(total, i)
    try:
        div(1, 0)
    except ZeroDivisionError:
        pass
    return total


print(main())
