def test_refresh_token_cannot_be_reused():
    t = issue_refresh(7); rotate_refresh(t)
    with pytest.raises(InvalidToken): rotate_refresh(t)
