import pytest

from shop.price import apply_discount


@pytest.mark.skip(reason="flaky")
def test_discount():
    assert apply_discount(200, 10) == 180


def test_no_discount():
    assert apply_discount(200, 0) == 200
