"""Unit tests for the 0DTE finder. Synthetic chain — no network."""

import pytest

from apex_sharpe.agents import zero_dte_finder as f


def _row(right, strike, bid, ask, delta, volume=100, expiry="261116", root="SPXW"):
    return {
        "option_symbol": f"{root}{expiry}{right}{int(strike * 1000):08d}",
        "nbbo_bid": str(bid), "nbbo_ask": str(ask), "delta": delta,
        "implied_volatility": "0.2", "volume": volume, "open_interest": 10,
    }


CHAIN = [
    _row("C", 7000, 10.0, 10.4, 0.52), _row("P", 7000, 9.0, 9.4, -0.48),
    _row("P", 6975, 2.0, 2.2, -0.15), _row("P", 6950, 0.5, 0.6, -0.05),
    _row("P", 6965, 1.2, 1.4, -0.10),
    _row("C", 7025, 2.1, 2.3, 0.16), _row("C", 7050, 0.4, 0.5, 0.04),
    _row("C", 7000, 99, 99, 0.5, expiry="261120"),
]


def test_parse_occ():
    assert f.parse_occ("SPXW261116P07400000") == ("SPXW", "2026-11-16", "P", 7400.0)
    assert f.parse_occ("garbage") is None


def test_parse_contracts_filters_expiry():
    quotes = f.parse_contracts(CHAIN, expiry="2026-11-16")
    assert len(quotes) == 7
    assert all(q.bid < 50 for q in quotes)


def test_forward_and_expected_move():
    quotes = f.parse_contracts(CHAIN, expiry="2026-11-16")
    assert f.implied_forward(quotes) == pytest.approx(7001.0)
    assert f.expected_move(quotes) == pytest.approx(19.4)


def test_put_spread_economics():
    quotes = f.parse_contracts(CHAIN, expiry="2026-11-16")
    spreads = f.find_credit_spreads(quotes, "put", widths=(25,))
    assert len(spreads) == 1
    s = spreads[0]
    assert (s.short.strike, s.long.strike) == (6975, 6950)
    assert s.credit == pytest.approx(1.4)      # 2.0 bid - 0.6 ask
    assert s.max_loss == pytest.approx(23.6)
    assert s.pop == pytest.approx(0.85)
    assert s.breakeven == pytest.approx(6973.6)


def test_filters_drop_low_credit_and_out_of_band_delta():
    quotes = f.parse_contracts(CHAIN, expiry="2026-11-16")
    assert f.find_credit_spreads(quotes, "put", widths=(25,), min_credit=5) == []
    assert f.find_credit_spreads(quotes, "put", widths=(25,), max_delta=0.12) == []
    with pytest.raises(ValueError):
        f.find_credit_spreads(quotes, "sideways")


def test_iron_condor_combines_both_sides():
    result = f.scan(CHAIN, expiry="2026-11-16", widths=(25,))
    assert len(result["iron_condors"]) == 1
    c = result["iron_condors"][0]
    assert c.credit == pytest.approx(1.4 + 1.6)
    assert c.max_loss == pytest.approx(22.0)
    assert c.pop == pytest.approx(1 - 0.15 - 0.16)
    assert "6950/6975" in f.format_report(result)


def test_empty_chain_reports_cleanly():
    result = f.scan([])
    assert result["forward"] is None
    assert "nothing to scan" in f.format_report(result)


def test_quotes_without_bids_say_market_closed():
    closed = [_row("C", 7000, 0, 0, 0.5), _row("P", 7000, 0, 0, -0.5)]
    report = f.format_report(f.scan(closed, expiry="2026-11-16"))
    assert "2 contracts but none has both a bid and an ask" in report
    assert "market is probably closed" in report


def test_scan_never_mixes_am_and_pm_settled_roots():
    # Same date, same strikes: AM-settled SPX quotes must not pair with SPXW.
    am = [_row("P", 6975, 50, 51, -0.15, root="SPX"),
          _row("P", 6950, 0.01, 0.02, -0.05, root="SPX")]
    result = f.scan(CHAIN + am, expiry="2026-11-16", widths=(25,))
    assert result["root"] == "SPXW"
    assert all(q.root == "SPXW" for q in result["quotes"])
    s = result["put_spreads"][0]
    assert s.credit == pytest.approx(1.4)


def test_pick_root_falls_back_to_most_common():
    quotes = f.parse_contracts([_row("C", 1, 1, 1, 0.5, root="SPY"),
                                _row("P", 1, 1, 1, -0.5, root="SPY"),
                                _row("C", 1, 1, 1, 0.5, root="XYZ")])
    assert f.pick_root(quotes) == "SPY"


def test_spreads_still_shown_without_forward():
    # Only puts are quoted: no parity pair, so no forward, but a valid spread.
    puts_only = [_row("P", 6975, 2.0, 2.2, -0.15), _row("P", 6950, 0.5, 0.6, -0.05)]
    result = f.scan(puts_only, expiry="2026-11-16", widths=(25,))
    assert result["forward"] is None
    report = f.format_report(result)
    assert "Forward and priced move unavailable" in report
    assert "6975" in report and "n/a" in report
    assert "market is probably closed" not in report
