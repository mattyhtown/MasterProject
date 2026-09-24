"""Permanent thesis lineage.

A thesis ID is minted once. Revisions, instruments, timing changes, and agent
handoffs remain descendants of that thesis. A new ID requires a new causal
mechanism and explicit Historian review.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4


@dataclass(frozen=True)
class Thesis:
    thesis_id: str
    title: str
    causal_mechanism: str
    parent_thesis_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class ThesisRevision:
    thesis_id: str
    revision_id: str
    reason: str
    changed_assumptions: tuple[str, ...]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def revise(thesis: Thesis, reason: str, changed_assumptions: tuple[str, ...]) -> ThesisRevision:
    """Revise without minting a new thesis identity."""
    return ThesisRevision(
        thesis_id=thesis.thesis_id,
        revision_id=str(uuid4()),
        reason=reason,
        changed_assumptions=changed_assumptions,
    )


def may_mint_new_thesis(*, causal_mechanism_materially_different: bool, historian_approved: bool) -> bool:
    """Both conditions are mandatory. Parameter/timing/instrument changes do not qualify."""
    return causal_mechanism_materially_different and historian_approved
