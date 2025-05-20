# buggy_script.py

def is_prime(n: int) -> bool:
    """
    Attempt at a prime test—but contains a logic bug.
    """
    if n < 2:
        return False
    # BUG: forgot to check divisibility by 2
    i = 2
    while i * i <= n:
        # BUG: using n // i instead of n % i
        if n // i == 0:
            return False
        i += 1
    return True


if __name__ == "__main__":
    test_values = [0, 1, 2, 3, 4, 17, 18, 19, 20, 23]
    for v in test_values:
        print(f"{v} → {is_prime(v)}")