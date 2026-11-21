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
    assert "2 contracts but none has a bid or an ask" in report
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


def test_pick_root_prefers_pm_settled_sibling_for_other_indices():
    # NDX (AM) outnumbers NDXP (PM) here, but the PM series must still win.
    rows = [_row("C", 20000, 1, 1, 0.5, root="NDX"),
            _row("P", 20000, 1, 1, -0.5, root="NDX"),
            _row("C", 20000, 1, 1, 0.5, root="NDXP")]
    assert f.pick_root(f.parse_contracts(rows)) == "NDXP"


def test_crossed_quotes_are_never_priced():
    # Long leg's ask (0.10) below its bid (0.60): a bad quote that would
    # inflate the credit to 1.90.
    rows = [_row("P", 6975, 2.0, 2.2, -0.15), _row("P", 6950, 0.6, 0.1, -0.05)]
    result = f.scan(rows, expiry="2026-11-16", widths=(25,))
    assert result["put_spreads"] == []


def test_scan_refuses_to_mix_expirations():
    with pytest.raises(ValueError, match="expirations"):
        f.scan(CHAIN)  # CHAIN holds 16 Nov and 20 Nov rows


def test_am_settled_only_chain_is_not_scanned():
    rows = [_row("P", 6975, 2.0, 2.2, -0.15, root="SPX"),
            _row("P", 6950, 0.5, 0.6, -0.05, root="SPX")]
    result = f.scan(rows, expiry="2026-11-16", widths=(25,))
    assert result["root"] == "" and result["put_spreads"] == []
    assert "AM-settled" in f.format_report(result)


def test_one_sided_quotes_are_not_reported_as_closed():
    # Short has only a bid, long has only an ask: still a priceable spread.
    rows = [_row("P", 6975, 2.0, 0, -0.15), _row("P", 6950, 0, 0.6, -0.05)]
    result = f.scan(rows, expiry="2026-11-16", widths=(25,))
    report = f.format_report(result)
    assert "probably closed" not in report
    assert "6975" in report


def test_save_chart_rejects_result_without_forward(tmp_path):
    pytest.importorskip("matplotlib")
    rows = [_row("P", 6975, 2.0, 2.2, -0.15), _row("P", 6950, 0.5, 0.6, -0.05)]
    result = f.scan(rows, expiry="2026-11-16", widths=(25,))
    with pytest.raises(ValueError, match="forward"):
        f.save_chart(result, str(tmp_path / "x.png"))


def test_non_finite_feed_numbers_are_treated_as_missing():
    row = _row("P", 6975, 2.0, 2.2, -0.15)
    row.update(volume="NaN", open_interest="Infinity", nbbo_ask="inf", delta=10 ** 400)
    (q,) = f.parse_contracts([row])
    assert (q.volume, q.open_interest, q.ask, q.delta) == (0, 0, 0.0, 0.0)


def test_fractional_strikes_match_exactly_and_print_exactly():
    # 16.10 - 10 is not exactly 6.10 in floating point.
    rows = [_row("P", 16.1, 2.0, 2.1, -0.15), _row("P", 6.1, 0.2, 0.3, -0.02),
            _row("P", 590.5, 2.0, 2.1, -0.15), _row("P", 580.5, 0.2, 0.3, -0.02)]
    result = f.scan(rows, expiry="2026-11-16", widths=(10,), min_credit=0.5)
    shorts = {s.short.strike for s in result["put_spreads"]}
    assert shorts == {16.1, 590.5}
    report = f.format_report(result)
    assert "590.5" in report and "580.5" in report and "16.1" in report


def test_crossed_pair_does_not_set_forward_or_move():
    rows = [_row("C", 7000, 11.8, 12.2, 0.5), _row("P", 7000, 9.8, 10.2, -0.5),
            _row("C", 7010, 27.0, 3.0, 0.4), _row("P", 7010, 14.8, 15.2, -0.6)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] == pytest.approx(7002.0)
    assert result["expected_move"] == pytest.approx(22.0)


def test_far_strike_straddle_is_not_reported_as_move():
    rows = [_row("C", 6950, 50.4, 50.6, 0.99), _row("P", 6950, 0.1, 0.2, -0.01)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] == pytest.approx(7000.35)
    assert result["expected_move"] is None


def test_non_positive_forward_is_unavailable_and_report_still_renders():
    rows = [_row("C", 100, 1.4, 1.6, 0.5), _row("P", 100, 101.4, 101.6, -0.5)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] is None
    f.format_report(result)  # must not raise ZeroDivisionError


def test_condor_never_pairs_overlapping_short_strikes():
    put = f.CreditSpread("put", f.Quote(7010, "P", 6, 6.2, -0.15, .2, 1, 1),
                         f.Quote(7000, "P", 1, 1.2, -0.1, .2, 1, 1))
    call = f.CreditSpread("call", f.Quote(6990, "C", 6, 6.2, 0.15, .2, 1, 1),
                          f.Quote(7000, "C", 1, 1.2, 0.1, .2, 1, 1))
    assert f.find_iron_condors([put], [call]) == []


def test_credits_are_priced_in_cents():
    # 40.30 - 30.30 must equal the 10-point width exactly (zero max loss, so
    # excluded), and 0.70 - 0.20 must meet a 0.50 minimum.
    rows = [_row("P", 7000, 40.30, 40.5, -0.15), _row("P", 6990, 30.0, 30.30, -0.1),
            _row("P", 6900, 0.70, 0.8, -0.15), _row("P", 6890, 0.1, 0.20, -0.1)]
    result = f.scan(rows, expiry="2026-11-16", widths=(10,), min_credit=0.5)
    shorts = [s.short.strike for s in result["put_spreads"]]
    assert shorts == [6900]
    assert result["put_spreads"][0].credit == 0.5


def test_live_series_beats_larger_unquoted_series():
    rows = [_row("C", 7000, 0, 0, 0.5, root="XSP"), _row("P", 7000, 0, 0, -0.5, root="XSP"),
            _row("C", 7010, 0, 0, 0.4, root="XSP"),
            _row("C", 7000, 11.8, 12.2, 0.5), _row("P", 7000, 9.8, 10.2, -0.5)]
    assert f.pick_root(f.parse_contracts(rows)) == "SPXW"


def test_vix_options_are_treated_as_am_settled():
    rows = [_row("C", 20, 1, 1.1, 0.5, root="VIX"), _row("C", 20, 1, 1.1, 0.5, root="VIXW")]
    assert f.pick_root(f.parse_contracts(rows)) == ""


def test_spreads_are_ranked_by_score_not_strike():
    rows = [_row("P", 7000, 3.0, 3.1, -0.20), _row("P", 6990, 1.0, 1.1, -0.12),
            _row("P", 6980, 0.9, 1.0, -0.10), _row("P", 6970, 0.1, 0.2, -0.06)]
    spreads = f.find_credit_spreads(f.parse_contracts(rows), "put", widths=(10,),
                                    min_credit=0.5)
    scores = [s.score for s in spreads]
    assert len(spreads) == 2 and scores == sorted(scores, reverse=True)
    assert [s.short.strike for s in spreads] == [7000, 6980]


def test_condor_delta_gap_boundary_is_inclusive():
    put = f.CreditSpread("put", f.Quote(6950, "P", 2, 2.1, -0.15, .2, 1, 1),
                         f.Quote(6940, "P", 1, 1.1, -0.1, .2, 1, 1))
    call = f.CreditSpread("call", f.Quote(7050, "C", 2, 2.1, 0.20, .2, 1, 1),
                          f.Quote(7060, "C", 1, 1.1, 0.1, .2, 1, 1))
    assert len(f.find_iron_condors([put], [call])) == 1  # |0.20 - 0.15| == 0.05


def test_save_chart_writes_png_and_adds_suffix(tmp_path):
    pytest.importorskip("matplotlib")
    result = f.scan(CHAIN[:-1], expiry="2026-11-16", widths=(25,))
    written = f.save_chart(result, str(tmp_path / "report"))
    assert written == str(tmp_path / "report.png")
    with open(written, "rb") as fh:
        assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(ValueError, match=".png"):
        f.save_chart(result, str(tmp_path / "report.jpg"))


def test_condor_delta_gap_just_outside_boundary_is_rejected():
    put = f.CreditSpread("put", f.Quote(6950, "P", 2, 2.1, -0.15, .2, 1, 1),
                         f.Quote(6940, "P", 1, 1.1, -0.1, .2, 1, 1))
    call = f.CreditSpread("call", f.Quote(7050, "C", 2, 2.1, 0.201, .2, 1, 1),
                          f.Quote(7060, "C", 1, 1.1, 0.1, .2, 1, 1))
    assert f.find_iron_condors([put], [call]) == []


def test_wide_quotes_do_not_pass_for_the_at_the_money_pair():
    rows = [_row("C", 6975, 26.0, 26.5, 0.8), _row("P", 6975, 0.25, 52.25, -0.2),
            _row("C", 7000, 10.75, 11.25, 0.5), _row("P", 7000, 9.75, 10.25, -0.5)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] == pytest.approx(7001.0)
    assert result["expected_move"] == pytest.approx(21.0)


def test_forward_is_reported_when_only_the_move_is_missing():
    rows = [_row("C", 6950, 50.4, 50.6, 0.99), _row("P", 6950, 0.1, 0.2, -0.01)]
    report = f.format_report(f.scan(rows, expiry="2026-11-16"))
    assert "Forward 7,000.4" in report and "priced move unavailable" in report


def test_crossed_quotes_do_not_make_a_series_look_live():
    rows = [_row("C", 7000, 5, 1, 0.5, root="XSP"), _row("P", 7000, 0, 0, -0.5, root="XSP"),
            _row("C", 7010, 0, 0, 0.4, root="XSP"),
            _row("C", 7000, 11.8, 12.2, 0.5), _row("P", 7000, 9.8, 10.2, -0.5)]
    assert f.pick_root(f.parse_contracts(rows)) == "SPXW"


def test_tiny_fractional_width_keeps_its_exact_max_loss():
    rows = [_row("P", 100.504, 0.70, 0.8, -0.15), _row("P", 100.0, 0.1, 0.20, -0.05)]
    spreads = f.find_credit_spreads(f.parse_contracts(rows), "put", widths=(0.504,),
                                    min_credit=0.5)
    assert len(spreads) == 1
    assert spreads[0].max_loss == pytest.approx(0.004)
    report = f.format_report({"root": "X", "quotes": f.parse_contracts(rows),
                              "forward": None, "expected_move": None,
                              "put_spreads": spreads, "call_spreads": [],
                              "iron_condors": []})
    assert " 0.004 " in report


@pytest.mark.parametrize("bad", [float("inf"), 0, -10, 10.0004])
def test_invalid_widths_are_rejected(bad):
    with pytest.raises(ValueError):
        f.find_credit_spreads([], "put", widths=(bad,))


def test_duplicate_widths_build_each_spread_once():
    rows = [_row("P", 6975, 2.0, 2.2, -0.15), _row("P", 6950, 0.5, 0.6, -0.05)]
    spreads = f.find_credit_spreads(f.parse_contracts(rows), "put", widths=(25, 25.0))
    assert len(spreads) == 1


@pytest.mark.parametrize("strike, text", [(7000.0, "7000"), (590.5, "590.5"),
                                          (6000.001, "6000.001"), (6000.002, "6000.002"),
                                          (16.1, "16.1")])
def test_fmt_strike_keeps_every_occ_decimal(strike, text):
    assert f.fmt_strike(strike) == text


def test_straddle_uses_strike_nearest_forward_not_tightest_pair():
    # Both pairs imply a 7,000 forward; the tighter 7,005 pair sets it, but the
    # move must come from the 7,000 straddle (11 points), not be suppressed.
    # The 7,000 mids alone would imply 7,001, so a forward of 7,000 proves the
    # tighter 7,005 pair anchored it.
    rows = [_row("C", 7000, 4.0, 8.0, 0.5), _row("P", 7000, 3.0, 7.0, -0.5),
            _row("C", 7005, 1.75, 2.25, 0.4), _row("P", 7005, 6.75, 7.25, -0.6)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] == pytest.approx(7000.0)
    assert result["expected_move"] == pytest.approx(11.0)


def test_straddle_that_disagrees_with_forward_is_not_used():
    # Tight 7,005 pair sets forward 7,000; the 7,000 pair's mids imply ~7,039,
    # so it must not price the move. The 7,005 straddle (13) is used instead.
    rows = [_row("C", 7005, 3.75, 4.25, 0.4), _row("P", 7005, 8.75, 9.25, -0.6),
            _row("C", 7000, 39.9, 40.1, 0.9), _row("P", 7000, 0.9, 1.1, -0.1)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] == pytest.approx(7000.0)
    assert result["expected_move"] == pytest.approx(13.0)


def test_equally_near_strikes_are_both_tried():
    # Forward 7,002.5: 7,000 straddle (4.5) fails the distance guard, 7,005
    # straddle (5.5) passes, so the move is 5.5 rather than unavailable.
    rows = [_row("C", 7000, 3.45, 3.55, 0.6), _row("P", 7000, 0.95, 1.05, -0.4),
            _row("C", 7005, 1.45, 1.55, 0.4), _row("P", 7005, 3.95, 4.05, -0.6)]
    result = f.scan(rows, expiry="2026-11-16")
    assert result["forward"] == pytest.approx(7002.5)
    assert result["expected_move"] == pytest.approx(5.5)


@pytest.mark.parametrize("value, text", [(8.9, "8.90"), (0.004, "0.004"),
                                         (0.014, "0.014"), (0.0, "0.00")])
def test_fmt_points_shows_third_decimal_when_it_matters(value, text):
    assert f.fmt_points(value) == text
