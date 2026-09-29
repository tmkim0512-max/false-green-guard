def apply_discount(price: float, percent: float) -> float:
    """Price after a percentage discount, e.g. apply_discount(200, 10) == 180."""
    return price - price * percent  # bug: percent is not divided by 100
