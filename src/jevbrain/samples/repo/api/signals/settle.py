def settle(signal, trades, window):
    """Settle from the first trade in the same pool at or after block + window."""
    later = [t for t in trades if t.pool == signal.pool and t.block >= signal.block + window]
    if not later:
        return None
    exit_trade = max(later, key=lambda t: t.block)   # BUG: should be the first, not the last
    return exit_trade.price / signal.entry - 1
