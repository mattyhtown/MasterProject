"""Governance primitives for capital protection and research graduation."""

from .edge_ledger import (
    Book, EdgeClass, EdgeRecord, GraduationEvidence, GraduationStage,
    HistorianEvent, append_historian_event, evaluate_graduation,
)
from .capital_allocator import (
    AllocationDecision, AllocationPolicy, AllocationRequest, allocate,
)
from .thesis_lineage import (
    Thesis, ThesisRevision, may_mint_new_thesis, revise,
)

__all__ = [
    "Book", "EdgeClass", "EdgeRecord", "GraduationEvidence", "GraduationStage",
    "HistorianEvent", "append_historian_event", "evaluate_graduation",
    "AllocationDecision", "AllocationPolicy", "AllocationRequest", "allocate",
    "Thesis", "ThesisRevision", "may_mint_new_thesis", "revise",
]
