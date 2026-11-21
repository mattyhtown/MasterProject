"""
0DTE SPX finder — rank same-day credit spreads and iron condors.

Read-only research tool. It takes option-chain rows (Unusual Whales
``option-contracts`` shape), builds defined-risk credit structures, and ranks
them. It never places or routes orders.

"POP" is 1 - |delta of the short strike|, a delta-based proxy for the
risk-neutral chance of expiring out of the money. Delta is N(d1) while that
probability is N(d2); they differ by sigma*sqrt(T), which is a fraction of a
percentage point for same-day options. At fair prices
every structure has roughly zero expected value before costs, so the score is
a screening heuristic (return on risk x probability of keeping the credit),
not a claim of edge.
"""

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

DEFAULT_WIDTHS = (10.0, 25.0, 50.0)

# Index option roots that settle on the next morning's opening print (AM).
# Their PM-settled siblings (SPXW, NDXP, RUTW, ...) are what a same-day
# intraday scan wants.
AM_SETTLED_ROOTS = frozenset({"SPX", "NDX", "RUT", "DJX", "MXEA", "MXEF", "VIX", "VIXW"})


_EPS = 1e-9  # below this a price difference is float noise, not money


def _ticks(strike: float) -> int:
    """Strike in OCC thousandths of a point, for exact matching."""
    return int(round(strike * 1000))


def fmt_strike(strike: float) -> str:
    """Strike as listed: no decimals for whole strikes, otherwise exact."""
    return f"{strike:.0f}" if strike == int(strike) else f"{strike:g}"


@dataclass(frozen=True)
class Quote:
    """One option contract's quote."""

    strike: float
    right: str  # "C" or "P"
    bid: float
    ask: float
    delta: float
    iv: float
    volume: int
    open_interest: int
    root: str = ""
    expiry: str = ""

    @property
    def crossed(self) -> bool:
        """Ask below bid: a stale or bad quote that must not be priced."""
        return self.ask > 0 and self.bid > self.ask

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class CreditSpread:
    """Short vertical: sell ``short``, buy ``long`` further out of the money."""

    side: str  # "put" or "call"
    short: Quote
    long: Quote

    @property
    def width(self) -> float:
        return abs(_ticks(self.short.strike) - _ticks(self.long.strike)) / 1000

    @property
    def credit(self) -> float:
        """Natural credit: sell at the bid, buy at the ask (rounded to cents)."""
        return round(self.short.bid - self.long.ask, 2)

    @property
    def credit_mid(self) -> float:
        return self.short.mid - self.long.mid

    @property
    def max_loss(self) -> float:
        return self.width - self.credit

    @property
    def return_on_risk(self) -> float:
        return self.credit / self.max_loss if self.max_loss > _EPS else 0.0

    @property
    def pop(self) -> float:
        """Delta proxy for the chance the short strike expires out of the money."""
        return 1.0 - abs(self.short.delta)

    @property
    def breakeven(self) -> float:
        if self.side == "put":
            return self.short.strike - self.credit
        return self.short.strike + self.credit

    @property
    def score(self) -> float:
        return self.return_on_risk * self.pop


@dataclass(frozen=True)
class IronCondor:
    """A put credit spread plus a call credit spread of the same width."""

    put: CreditSpread
    call: CreditSpread

    @property
    def width(self) -> float:
        return self.put.width

    @property
    def credit(self) -> float:
        return round(self.put.credit + self.call.credit, 2)

    @property
    def max_loss(self) -> float:
        return self.width - self.credit

    @property
    def return_on_risk(self) -> float:
        return self.credit / self.max_loss if self.max_loss > 0 else 0.0

    @property
    def pop(self) -> float:
        """Delta proxy for the chance price expires between the short strikes."""
        return max(0.0, 1.0 - abs(self.put.short.delta) - abs(self.call.short.delta))

    @property
    def score(self) -> float:
        return self.return_on_risk * self.pop


def parse_occ(symbol: str) -> Optional[Tuple[str, str, str, float]]:
    """Split an OCC symbol into (root, YYYY-MM-DD expiry, right, strike)."""
    m = OCC_RE.match(symbol or "")
    if not m:
        return None
    root, ymd, right, strike = m.groups()
    expiry = f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}"
    return root, expiry, right, int(strike) / 1000.0


def _num(value: Any) -> float:
    """Feed number as a float; missing, malformed, NaN or infinite becomes 0."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def parse_contracts(rows: Iterable[Dict[str, Any]], expiry: str = "") -> List[Quote]:
    """Convert raw chain rows into quotes, optionally keeping one expiry."""
    quotes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_occ(str(row.get("option_symbol", "")))
        if not parsed:
            continue
        root, row_expiry, right, strike = parsed
        if expiry and row_expiry != expiry:
            continue
        quotes.append(Quote(
            strike=strike,
            right=right,
            bid=_num(row.get("nbbo_bid")),
            ask=_num(row.get("nbbo_ask")),
            delta=_num(row.get("delta")),
            iv=_num(row.get("implied_volatility")),
            volume=int(_num(row.get("volume"))),
            open_interest=int(_num(row.get("open_interest"))),
            root=root,
            expiry=row_expiry,
        ))
    return sorted(quotes, key=lambda q: (q.strike, q.right))


def pick_root(quotes: Sequence[Quote]) -> str:
    """Choose one settlement series so spreads never mix AM and PM settlement.

    On monthly-expiry dates an AM-settled root (SPX, NDX, RUT, ...) shares
    its expiry date with a PM-settled sibling (SPXW, NDXP, RUTW, ...). A
    same-day scan wants the PM-settled series. AM-settled roots are never
    chosen: on their expiry date they settled at the morning open. Among the
    rest, the root with the most contracts wins; "" if none is eligible.
    """
    counts: Dict[str, int] = {}
    live: Dict[str, int] = {}
    for q in quotes:
        if q.root not in AM_SETTLED_ROOTS:
            counts[q.root] = counts.get(q.root, 0) + 1
            if (q.bid > 0 or q.ask > 0) and not q.crossed:
                live[q.root] = live.get(q.root, 0) + 1
    if not counts:
        return ""
    # A series with live quotes beats a larger one that has none.
    return max(counts, key=lambda r: (live.get(r, 0) > 0, counts[r]))


def has_live_quotes(quotes: Sequence[Quote]) -> bool:
    """Any bid or ask at all. Outside market hours every one is zero."""
    return any(q.bid > 0 or q.ask > 0 for q in quotes)


def _two_sided_pairs(quotes: Sequence[Quote]) -> List[Tuple[Quote, Quote]]:
    calls = {q.strike: q for q in quotes if q.right == "C"}
    puts = {q.strike: q for q in quotes if q.right == "P"}
    pairs = []
    for strike in sorted(set(calls) & set(puts)):
        call, put = calls[strike], puts[strike]
        if (call.bid > 0 and put.bid > 0 and call.ask > 0 and put.ask > 0
                and not call.crossed and not put.crossed):
            pairs.append((call, put))
    return pairs


def _atm_pair(quotes: Sequence[Quote]) -> Optional[Tuple[Quote, Quote]]:
    pairs = _two_sided_pairs(quotes)
    if not pairs:
        return None
    # Nearest the money is where call and put mids meet; quote width is added
    # so a wide, uninformative market can't pass for the at-the-money pair.
    return min(pairs, key=lambda cp: abs(cp[0].mid - cp[1].mid)
               + (cp[0].ask - cp[0].bid) + (cp[1].ask - cp[1].bid))


def implied_forward(quotes: Sequence[Quote]) -> Optional[float]:
    """Forward price from put-call parity at the strike nearest the money.

    None if no clean call/put pair is quoted or the result is not positive.
    """
    pair = _atm_pair(quotes)
    if pair is None:
        return None
    call, put = pair
    forward = call.strike + call.mid - put.mid
    return forward if math.isfinite(forward) and forward > 0 else None


def expected_move(quotes: Sequence[Quote]) -> Optional[float]:
    """At-the-money straddle mid: the market's priced move into the close.

    None when the nearest quoted pair is so far from the forward that
    intrinsic value, not time value, would make up most of the straddle.
    """
    pair = _atm_pair(quotes)
    forward = implied_forward(quotes)
    if pair is None or forward is None:
        return None
    call, put = pair
    straddle = call.mid + put.mid
    if abs(call.strike - forward) > straddle / 2:
        return None
    return straddle


def find_credit_spreads(
    quotes: Sequence[Quote],
    side: str,
    widths: Sequence[float] = DEFAULT_WIDTHS,
    min_delta: float = 0.05,
    max_delta: float = 0.20,
    min_credit: float = 0.50,
    min_volume: int = 0,
) -> List[CreditSpread]:
    """Build every credit spread on ``side`` that passes the filters, best first."""
    if side not in ("put", "call"):
        raise ValueError("side must be 'put' or 'call'")
    right = "P" if side == "put" else "C"
    direction = -1.0 if side == "put" else 1.0
    for w in widths:
        if not (isinstance(w, (int, float)) and math.isfinite(w) and w > 0):
            raise ValueError(f"width {w!r} must be a finite positive number")
        if abs(w * 1000 - round(w * 1000)) > 1e-6:
            raise ValueError(f"width {w!r} is finer than OCC strike precision (0.001)")
    tick_widths = sorted({_ticks(w) for w in widths})
    by_strike = {_ticks(q.strike): q for q in quotes if q.right == right}
    spreads = []
    for short in by_strike.values():
        if not min_delta <= abs(short.delta) <= max_delta:
            continue
        if short.bid <= 0 or short.crossed or short.volume < min_volume:
            continue
        for tick_width in tick_widths:
            long = by_strike.get(_ticks(short.strike) + int(direction) * tick_width)
            if long is None or long.ask <= 0 or long.crossed or long.volume < min_volume:
                continue
            spread = CreditSpread(side=side, short=short, long=long)
            if spread.credit >= min_credit - _EPS and spread.max_loss > _EPS:
                spreads.append(spread)
    return sorted(spreads, key=lambda s: s.score, reverse=True)


def find_iron_condors(
    put_spreads: Sequence[CreditSpread],
    call_spreads: Sequence[CreditSpread],
    max_delta_gap: float = 0.05,
) -> List[IronCondor]:
    """Pair equal-width put and call spreads with similar short deltas."""
    condors = []
    for put in put_spreads:
        for call in call_spreads:
            if _ticks(put.width) != _ticks(call.width):
                continue
            if put.short.strike >= call.short.strike:
                continue  # overlapping wings could both lose
            if abs(abs(put.short.delta) - abs(call.short.delta)) > max_delta_gap + 1e-9:
                continue
            condor = IronCondor(put=put, call=call)
            if condor.max_loss > _EPS:
                condors.append(condor)
    return sorted(condors, key=lambda c: c.score, reverse=True)


def scan(
    rows: Iterable[Dict[str, Any]],
    expiry: str = "",
    widths: Sequence[float] = DEFAULT_WIDTHS,
    min_delta: float = 0.05,
    max_delta: float = 0.20,
    min_credit: float = 0.50,
    min_volume: int = 0,
) -> Dict[str, Any]:
    """Run the full scan over raw chain rows.

    Raises ValueError if ``expiry`` is omitted and the rows span several
    expirations, since spreads must not pair contracts from different dates.
    """
    quotes = parse_contracts(rows, expiry=expiry)
    expiries = {q.expiry for q in quotes}
    if len(expiries) > 1:
        raise ValueError(f"rows span {len(expiries)} expirations; pass expiry=")
    root = pick_root(quotes)
    quotes = [q for q in quotes if q.root == root]
    kwargs = dict(widths=widths, min_delta=min_delta, max_delta=max_delta,
                  min_credit=min_credit, min_volume=min_volume)
    puts = find_credit_spreads(quotes, "put", **kwargs)
    calls = find_credit_spreads(quotes, "call", **kwargs)
    return {
        "root": root,
        "quotes": quotes,
        "forward": implied_forward(quotes),
        "expected_move": expected_move(quotes),
        "put_spreads": puts,
        "call_spreads": calls,
        "iron_condors": find_iron_condors(puts, calls),
    }


def format_report(result: Dict[str, Any], top: int = 5) -> str:
    """Plain-text tables for the CLI."""
    lines = []
    forward, move = result["forward"], result["expected_move"]
    if not result["quotes"]:
        return ("  No same-day PM-settled contracts in the chain — nothing to scan."
                "\n  (AM-settled index options settled at this morning's open.)")
    if not has_live_quotes(result["quotes"]):
        return (f"  {len(result['quotes'])} contracts but none has a bid or an ask."
                "\n  The market is probably closed: same-day contracts trade"
                " 9:30-16:00 ET.")
    root = result.get("root") or "-"
    if forward is None:
        lines.append(f"  Forward and priced move unavailable (no clean call/put pair"
                     f" at a common strike)   {root}   {len(result['quotes'])} contracts")
    elif move is None:
        lines.append(f"  Forward {forward:,.1f}   priced move unavailable (nearest quoted"
                     f" pair is far from the forward)   {root}"
                     f"   {len(result['quotes'])} contracts")
    else:
        lines.append(f"  Forward {forward:,.1f}   priced move into close ±{move:.1f}"
                     f" ({move / forward:.2%})   {root}   {len(result['quotes'])} contracts")
    header = (f"  {'Short':>7} {'Long':>7} {'Wid':>4} {'Δ':>6} {'Credit':>7}"
              f" {'MaxLoss':>8} {'RoR':>6} {'POP':>6} {'Dist':>6} {'Vol':>7}")
    for title, key in (("Put credit spreads", "put_spreads"),
                       ("Call credit spreads", "call_spreads")):
        lines.append(f"\n  {title}")
        spreads = result[key][:top]
        if not spreads:
            lines.append("    none passed the filters")
            continue
        lines.append(header)
        for s in spreads:
            if forward is not None and move:
                dist_txt = f"{abs(s.short.strike - forward) / move:>5.1f}x"
            else:
                dist_txt = f"{'n/a':>6}"
            lines.append(
                f"  {fmt_strike(s.short.strike):>7} {fmt_strike(s.long.strike):>7}"
                f" {fmt_strike(s.width):>4}"
                f" {abs(s.short.delta):>6.2f} {s.credit:>7.2f} {s.max_loss:>8.2f}"
                f" {s.return_on_risk:>6.1%} {s.pop:>6.1%} {dist_txt}"
                f" {s.short.volume:>7,}")
    lines.append("\n  Iron condors")
    condors = result["iron_condors"][:top]
    if not condors:
        lines.append("    none passed the filters")
    else:
        lines.append(f"  {'Put':>13} {'Call':>13} {'Wid':>4} {'Credit':>7}"
                     f" {'MaxLoss':>8} {'RoR':>6} {'POP':>6}")
        for c in condors:
            put = f"{fmt_strike(c.put.long.strike)}/{fmt_strike(c.put.short.strike)}"
            call = f"{fmt_strike(c.call.short.strike)}/{fmt_strike(c.call.long.strike)}"
            lines.append(f"  {put:>13} {call:>13} {fmt_strike(c.width):>4} {c.credit:>7.2f}"
                         f" {c.max_loss:>8.2f} {c.return_on_risk:>6.1%} {c.pop:>6.1%}")
    lines.append("\n  Credit = sell at bid, buy at ask. POP is a delta proxy:"
                 " spreads 1 - |Δ short|; condors 1 - |Δ short put| - |Δ short call|."
                 "\n  Dist = short strike distance in priced"
                 " moves. Research only — no orders are placed.")
    return "\n".join(lines)


def chart_path_for(path: str) -> str:
    """The PNG path a chart will be written to: adds .png, rejects other formats."""
    import os
    root, ext = os.path.splitext(path)
    if not ext:
        return path + ".png"
    if ext.lower() != ".png":
        raise ValueError(f"chart path must end in .png (got {ext})")
    return path


def save_chart(result: Dict[str, Any], path: str, title: str = "") -> str:
    """Write a two-panel PNG: volume by strike, and the candidate trade-off.

    Returns the path written (``.png`` is added when ``path`` has no suffix).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, muted, grid, surface = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    put_color, call_color = "#2a78d6", "#eb6834"
    chart_path_for(path)  # fail on a bad path before any plotting
    forward, move = result["forward"], result["expected_move"]
    if forward is None or move is None:
        raise ValueError("cannot chart a scan without a forward and priced move"
                         " (no quoted call/put pair at a common strike)")
    quotes = [q for q in result["quotes"] if abs(q.strike - forward) <= 4 * move]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 9), facecolor=surface)
    for ax in (ax1, ax2):
        ax.set_facecolor(surface)
        ax.grid(axis="y", color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#c3c2b7")
        ax.tick_params(colors=muted, length=0)

    ax1.axvspan(forward - move, forward + move, color="#f0efec", zorder=0)
    ax1.bar([q.strike for q in quotes if q.right == "C"],
            [q.volume / 1000 for q in quotes if q.right == "C"],
            width=3.5, color=call_color, label="Call volume")
    ax1.bar([q.strike for q in quotes if q.right == "P"],
            [-q.volume / 1000 for q in quotes if q.right == "P"],
            width=3.5, color=put_color, label="Put volume")
    ax1.axvline(forward, color=ink, linewidth=1)
    top = ax1.get_ylim()[1]
    ax1.text(forward, top, f" forward {forward:,.0f}", color=ink, va="top", fontsize=9)
    ax1.text(forward - move, ax1.get_ylim()[0], f" ±{move:.0f} priced move",
             color=muted, va="bottom", fontsize=9)
    for key, color in (("put_spreads", put_color), ("call_spreads", call_color)):
        if result[key]:
            best = result[key][0]
            ax1.axvline(best.short.strike, color=color, linewidth=1.5, linestyle="--")
            ax1.text(best.short.strike, top * 0.8, f" top short {fmt_strike(best.short.strike)}",
                     color=ink, fontsize=9)
    ax1.set_title(title or "0DTE volume by strike", loc="left", color=ink,
                  fontsize=12, fontweight="bold")
    ax1.set_ylabel("Contracts traded (thousands; puts below zero)", color=muted)
    ax1.set_xlabel("Strike", color=muted)
    ax1.legend(frameon=False, labelcolor=ink, loc="lower right")

    for key, color, label in (("put_spreads", put_color, "Put credit spreads"),
                              ("call_spreads", call_color, "Call credit spreads")):
        spreads = result[key]
        ax2.scatter([s.pop * 100 for s in spreads],
                    [s.return_on_risk * 100 for s in spreads],
                    s=45, color=color, edgecolor=surface, linewidth=1, label=label)
        if spreads:
            best = spreads[0]
            ax2.annotate(f"{fmt_strike(best.short.strike)}/{fmt_strike(best.long.strike)}",
                         (best.pop * 100, best.return_on_risk * 100),
                         textcoords="offset points", xytext=(8, 6),
                         color=ink, fontsize=9)
    ax2.set_title("Candidates: chance of keeping the credit vs return on risk",
                  loc="left", color=ink, fontsize=12, fontweight="bold")
    ax2.set_xlabel("POP: 1 - |delta| of the short strike (%), a delta proxy",
                   color=muted)
    ax2.set_ylabel("Return on risk at natural prices (%)", color=muted)
    ax2.legend(frameon=False, labelcolor=ink)

    fig.tight_layout()
    out = chart_path_for(path)
    try:
        fig.savefig(out, dpi=160, facecolor=surface, format="png")
    finally:
        plt.close(fig)
    return out
