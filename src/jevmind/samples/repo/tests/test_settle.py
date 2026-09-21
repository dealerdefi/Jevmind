def test_settles_from_first_trade_after_window():
    assert settle(sig(block=10), [t(20, 1.1), t(30, 2.0)], window=10) == pytest.approx(0.1)
