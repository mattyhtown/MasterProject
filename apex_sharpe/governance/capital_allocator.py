"""Deterministic capital allocation gates.

Agents may request capital. This module grants only the amount permitted by
book, graduation stage, thesis-family exposure, and portfolio risk limits.
It has no brokerage execution capability.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict
from .edge_ledger import Book, GraduationStage


@dataclass(frozen=True)
class AllocationPolicy:
    # Fractions of NAV. Conservative defaults; configure explicitly in production.
    lab_total_cap: float = 0.02
    lab_edge_cap: float = 0.0025
    small_capital_edge_cap: float = 0.005
    thesis_family_cap: float = 0.03
    edge_book_cap: float = 0.15
    gross_cap: float = 1.00


@dataclass(frozen=True)
class AllocationRequest:
    edge_id: str
    thesis_id: str
    book: Book
    stage: GraduationStage
    requested_fraction_nav: float
    current_edge_fraction_nav: float = 0.0
    current_thesis_fraction_nav: float = 0.0
    current_book_fraction_nav: float = 0.0
    current_gross_fraction_nav: float = 0.0
    data_integrity_passed: bool = False
    risk_approved: bool = False


@dataclass(frozen=True)
class AllocationDecision:
    edge_id: str
    thesis_id: str
    requested_fraction_nav: float
    approved_fraction_nav: float
    denied_fraction_nav: float
    reasons: tuple[str, ...]


def allocate(req: AllocationRequest, policy: AllocationPolicy = AllocationPolicy()) -> AllocationDecision:
    """Return a deterministic capital grant. Never increases requested risk."""
    if req.requested_fraction_nav < 0:
        raise ValueError("requested_fraction_nav cannot be negative")
    if req.stage in (GraduationStage.RESEARCH, GraduationStage.WALK_FORWARD, GraduationStage.SHADOW):
        return _decision(req, 0.0, "stage is research/shadow only")
    if not req.data_integrity_passed:
        return _decision(req, 0.0, "historical/data integrity gate failed")
    if not req.risk_approved:
        return _decision(req, 0.0, "risk approval missing")

    caps = [req.requested_fraction_nav]
    reasons = []

    gross_room = max(0.0, policy.gross_cap - req.current_gross_fraction_nav)
    caps.append(gross_room)
    if gross_room < req.requested_fraction_nav:
        reasons.append("portfolio gross cap")

    thesis_room = max(0.0, policy.thesis_family_cap - req.current_thesis_fraction_nav)
    caps.append(thesis_room)
    if thesis_room < req.requested_fraction_nav:
        reasons.append("thesis-family cap")

    if req.book == Book.LAB:
        lab_room = max(0.0, policy.lab_total_cap - req.current_book_fraction_nav)
        edge_room = max(0.0, policy.lab_edge_cap - req.current_edge_fraction_nav)
        caps += [lab_room, edge_room]
        if lab_room < req.requested_fraction_nav:
            reasons.append("laboratory aggregate cap")
        if edge_room < req.requested_fraction_nav:
            reasons.append("laboratory per-edge cap")
    elif req.book == Book.EDGE:
        book_room = max(0.0, policy.edge_book_cap - req.current_book_fraction_nav)
        caps.append(book_room)
        if book_room < req.requested_fraction_nav:
            reasons.append("edge-book cap")
        if req.stage == GraduationStage.SMALL_CAPITAL:
            edge_room = max(0.0, policy.small_capital_edge_cap - req.current_edge_fraction_nav)
            caps.append(edge_room)
            if edge_room < req.requested_fraction_nav:
                reasons.append("small-capital per-edge cap")

    approved = min(caps)
    return AllocationDecision(
        edge_id=req.edge_id,
        thesis_id=req.thesis_id,
        requested_fraction_nav=req.requested_fraction_nav,
        approved_fraction_nav=approved,
        denied_fraction_nav=max(0.0, req.requested_fraction_nav - approved),
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _decision(req: AllocationRequest, approved: float, reason: str) -> AllocationDecision:
    return AllocationDecision(
        edge_id=req.edge_id,
        thesis_id=req.thesis_id,
        requested_fraction_nav=req.requested_fraction_nav,
        approved_fraction_nav=approved,
        denied_fraction_nav=max(0.0, req.requested_fraction_nav - approved),
        reasons=(reason,),
    )
