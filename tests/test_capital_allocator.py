from apex_sharpe.governance.capital_allocator import AllocationPolicy, AllocationRequest, allocate
from apex_sharpe.governance.edge_ledger import Book, GraduationStage
from apex_sharpe.governance.thesis_lineage import Thesis, may_mint_new_thesis, revise


def test_shadow_gets_no_capital():
    d = allocate(AllocationRequest("E1","T1",Book.LAB,GraduationStage.SHADOW,.01,
        data_integrity_passed=True,risk_approved=True))
    assert d.approved_fraction_nav == 0


def test_lab_caps_are_hard():
    p=AllocationPolicy(lab_total_cap=.02,lab_edge_cap=.0025)
    d=allocate(AllocationRequest("E1","T1",Book.LAB,GraduationStage.SMALL_CAPITAL,.02,
        data_integrity_passed=True,risk_approved=True),p)
    assert d.approved_fraction_nav == .0025


def test_thesis_family_aggregates_risk():
    d=allocate(AllocationRequest("E2","T1",Book.EDGE,GraduationStage.SCALED,.02,
        current_thesis_fraction_nav=.025,data_integrity_passed=True,risk_approved=True))
    assert round(d.approved_fraction_nav,6) == .005


def test_revision_keeps_identity_and_remint_requires_historian():
    t=Thesis("T1","Vol edge","IV exceeds expected realized variance")
    r=revise(t,"change entry threshold",("iv_percentile",))
    assert r.thesis_id == "T1"
    assert not may_mint_new_thesis(causal_mechanism_materially_different=True,historian_approved=False)
    assert may_mint_new_thesis(causal_mechanism_materially_different=True,historian_approved=True)
