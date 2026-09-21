def emit(trades, watched):
    """Every buy by a watched wallet becomes a signal, in block order."""
    return [Signal.from_trade(t) for t in sorted(trades, key=block_of) if t.wallet in watched and t.is_buy]
