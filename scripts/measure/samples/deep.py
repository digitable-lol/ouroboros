reached = 0


def descend(n):
    global reached
    if n > reached:
        reached = n
    return descend(n + 1)
