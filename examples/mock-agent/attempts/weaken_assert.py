from shop.price import apply_discount


def test_discount():
    apply_discount(200, 10)
    assert True


def test_no_discount():
    assert apply_discount(200, 0) == 200
