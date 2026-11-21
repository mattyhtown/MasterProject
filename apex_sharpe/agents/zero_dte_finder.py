"""
0DTE SPX finder — rank same-day credit spreads and iron condors.

Read-only research tool. It takes option-chain rows (Unusual Whales
``option-contracts`` shape), builds defined-risk credit structures, and ranks
them. It never places or routes orders.

Probabilities are the chain's own deltas, i.e. risk-neutral. At fair prices
every structure has roughly zero expected value before costs, so the score is
a screening heuristic (return on risk x probability of keeping the credit),
not a claim of edge.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")

DEFAULT_WIDTHS = (10.0, 25.0, 50.0)

# Index option roots that settle on the next morning's opening print (AM).
# Their PM-settled siblings (SPXW, NDXP, RUTW, ...) are what a same-day
# intraday scan wants.
AM_SETTLED_ROOTS = frozenset({"SPX", "NDX", "RUT", "DJX", "MXEA", "MXEF"})


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
        return abs(self.short.strike - self.long.strike)

    @property
    def credit(self) -> float:
        """Natural credit: sell at the bid, buy at the ask."""
        return self.short.bid - self.long.ask

    @property
    def credit_mid(self) -> float:
        return self.short.mid - self.long.mid

    @property
    def max_loss(self) -> float:
        return self.width - self.credit

    @property
    def return_on_risk(self) -> float:
        return self.credit / self.max_loss if self.max_loss > 0 else 0.0

    @property
    def pop(self) -> float:
        """Risk-neutral chance the short strike finishes out of the money."""
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
        return self.put.credit + self.call.credit

    @property
    def max_loss(self) -> float:
        return self.width - self.credit

    @property
    def return_on_risk(self) -> float:
        return self.credit / self.max_loss if self.max_loss > 0 else 0.0

    @property
    def pop(self) -> float:
        """Risk-neutral chance price finishes between the short strikes."""
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
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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
        ))
    return sorted(quotes, key=lambda q: (q.strike, q.right))


def pick_root(quotes: Sequence[Quote]) -> str:
    """Choose one settlement series so spreads never mix AM and PM settlement.

    On monthly-expiry dates an AM-settled root (SPX, NDX, RUT, ...) shares
    its expiry date with a PM-settled sibling (SPXW, NDXP, RUTW, ...). A
    same-day scan wants the PM-settled series, so AM-settled roots rank last;
    among the rest, the root with the most contracts wins.
    """
    counts: Dict[str, int] = {}
    for q in quotes:
        counts[q.root] = counts.get(q.root, 0) + 1
    if not counts:
        return ""
    return max(counts, key=lambda r: (r not in AM_SETTLED_ROOTS, counts[r]))


def has_two_sided_quotes(quotes: Sequence[Quote]) -> bool:
    return any(q.bid > 0 and q.ask > 0 for q in quotes)


def _two_sided_pairs(quotes: Sequence[Quote]) -> List[Tuple[Quote, Quote]]:
    calls = {q.strike: q for q in quotes if q.right == "C"}
    puts = {q.strike: q for q in quotes if q.right == "P"}
    pairs = []
    for strike in sorted(set(calls) & set(puts)):
        call, put = calls[strike], puts[strike]
        if call.bid > 0 and put.bid > 0 and call.ask > 0 and put.ask > 0:
            pairs.append((call, put))
    return pairs


def implied_forward(quotes: Sequence[Quote]) -> Optional[float]:
    """Forward price from put-call parity at the strike nearest the money."""
    pairs = _two_sided_pairs(quotes)
    if not pairs:
        return None
    call, put = min(pairs, key=lambda cp: abs(cp[0].mid - cp[1].mid))
    return call.strike + call.mid - put.mid


def expected_move(quotes: Sequence[Quote]) -> Optional[float]:
    """At-the-money straddle mid: the market's priced move into the close."""
    pairs = _two_sided_pairs(quotes)
    if not pairs:
        return None
    call, put = min(pairs, key=lambda cp: abs(cp[0].mid - cp[1].mid))
    return call.mid + put.mid


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
    by_strike = {q.strike: q for q in quotes if q.right == right}
    spreads = []
    for short in by_strike.values():
        if not min_delta <= abs(short.delta) <= max_delta:
            continue
        if short.bid <= 0 or short.volume < min_volume:
            continue
        for width in widths:
            long = by_strike.get(short.strike + direction * width)
            if long is None or long.ask <= 0 or long.volume < min_volume:
                continue
            spread = CreditSpread(side=side, short=short, long=long)
            if spread.credit >= min_credit and spread.max_loss > 0:
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
            if put.width != call.width:
                continue
            if abs(abs(put.short.delta) - abs(call.short.delta)) > max_delta_gap:
                continue
            condor = IronCondor(put=put, call=call)
            if condor.max_loss > 0:
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
    """Run the full scan over raw chain rows."""
    quotes = parse_contracts(rows, expiry=expiry)
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
        return "  No contracts in the chain — nothing to scan."
    if not has_two_sided_quotes(result["quotes"]):
        return (f"  {len(result['quotes'])} contracts but none has both a bid and an ask."
                "\n  The market is probably closed: same-day contracts trade"
                " 9:30-16:00 ET.")
    root = result.get("root") or "-"
    if forward is None or move is None:
        lines.append(f"  Forward and priced move unavailable (no quoted call/put pair"
                     f" at a common strike)   {root}   {len(result['quotes'])} contracts")
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
                f"  {s.short.strike:>7.0f} {s.long.strike:>7.0f} {s.width:>4.0f}"
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
            put = f"{c.put.long.strike:.0f}/{c.put.short.strike:.0f}"
            call = f"{c.call.short.strike:.0f}/{c.call.long.strike:.0f}"
            lines.append(f"  {put:>13} {call:>13} {c.width:>4.0f} {c.credit:>7.2f}"
                         f" {c.max_loss:>8.2f} {c.return_on_risk:>6.1%} {c.pop:>6.1%}")
    lines.append("\n  Credit = sell at bid, buy at ask. POP and Δ are risk-neutral"
                 " (from chain deltas).\n  Dist = short strike distance in priced"
                 " moves. Research only — no orders are placed.")
    return "\n".join(lines)


def save_chart(result: Dict[str, Any], path: str, title: str = "") -> None:
    """Write a two-panel PNG: volume by strike, and the candidate trade-off."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, muted, grid, surface = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    put_color, call_color = "#2a78d6", "#eb6834"
    forward, move = result["forward"], result["expected_move"]
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
            ax1.text(best.short.strike, top * 0.8, f" top short {best.short.strike:.0f}",
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
            ax2.annotate(f"{best.short.strike:.0f}/{best.long.strike:.0f}",
                         (best.pop * 100, best.return_on_risk * 100),
                         textcoords="offset points", xytext=(8, 6),
                         color=ink, fontsize=9)
    ax2.set_title("Candidates: chance of keeping the credit vs return on risk",
                  loc="left", color=ink, fontsize=12, fontweight="bold")
    ax2.set_xlabel("Risk-neutral chance short strike expires out of the money (%)",
                   color=muted)
    ax2.set_ylabel("Return on risk at natural prices (%)", color=muted)
    ax2.legend(frameon=False, labelcolor=ink)

    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=surface)
    plt.close(fig)
