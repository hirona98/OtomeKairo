from itertools import count


def id_allocator():
    numbers = count(1)
    return lambda kind: f"{kind}:{next(numbers)}"
