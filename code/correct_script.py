# correct_script.py this is not a buggy one

def is_prime(n: int) -> bool:
    """
    Returns True if n is a prime number, False otherwise.
    Handles edge cases for n < 2.
    """
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


if __name__ == "__main__":
    test_values = [0, 1, 2, 3, 4, 17, 18, 19, 20, 23]
    for v in test_values:
        print(f"{v} → {is_prime(v)}")
