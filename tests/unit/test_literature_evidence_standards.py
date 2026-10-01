"""Adversarial and evidentiary integrity tests for REX Evidence Standards (Standards 1 to 20).

Enforces the core REX Evidence Plane invariants:
1. No claim receives stronger evidentiary status than justified by persisted, attributable evidence.
2. Literature evidence informs REX reasoning, but literature retrieval ALONE can never constitute
   mechanical verification of a REX experimental claim.
3. Security and scientific evidence are orthogonal dimensions.
4. Comprehensive test coverage for all 15 scenarios enumerated in Evidence Standard 20.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.domain.models import (
    ClaimStatus,
    EvidenceNodeType,
    EvidenceRelationType,
    LiteratureSource,
)
from rex.evidence.graph import (
    CrossRunEvidenceError,
    EvidenceGraphService,
    InvalidRelationError,
    NodeNotFoundError,
)
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.literature.trust import (
    InjectionDetector,
    InjectionRiskLevel,
    assert_literature_cannot_execute,
)
from rex.persistence.database import Base
from rex.persistence.models import (
    ClaimModel,
    LiteratureSourceModel,
    ResearchRunModel,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def test_run(db_session: Session) -> ResearchRunModel:
    run = ResearchRunModel(
        id=f"run_ev_{uuid.uuid4().hex[:8]}",
        title="Evidence Standards Verification Run",
        research_question="Does literature alone satisfy verification?",
        status="ANALYSIS",
    )
    db_session.add(run)
    db_session.commit()
    return run


# ==============================================================================
# Evidence Standard 20: 15 Explicit Scenarios
# ==============================================================================


def test_standard_20_1_source_with_no_stable_identifier_rejected() -> None:
    """Scenario 1: Retrieved source with no stable identifier is rejected or flagged."""
    # LiteratureSource requires non-empty external_id
    with pytest.raises(ValidationError):
        LiteratureSource(
            research_run_id="run_1",
            provider="openalex",
            external_id="",  # Empty identifier
            title="Floating Paper",
        )

    # Whitespace-only external_id
    with pytest.raises(ValidationError):
        LiteratureSource(
            research_run_id="run_1",
            provider="openalex",
            external_id="   ",
            title="Floating Paper",
        )


def test_standard_20_2_claim_supported_only_by_title_similarity_not_verified(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 2: A claim supported only by title similarity to a paper cannot be marked verified."""
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Batch size scaling improves transformer generalization.",
        status="proposed",
    )
    source = LiteratureSourceModel(
        research_run_id=test_run.id,
        provider="arxiv",
        external_id="arxiv:2201.0001",
        title="Batch Size Scaling Improves Transformer Generalization",
        abstract="Discussion of scaling parameters.",
    )
    db_session.add_all([claim, source])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=source.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
        metadata={"similarity_metric": 0.99},
    )

    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(test_run.id)

    # Must fail because literature alone has no empirical execution/results
    assert not report.is_passed
    assert report.status == VerificationStatus.FAIL
    claim_res = report.claims_verified[0]
    assert not claim_res.is_lineage_intact
    assert any("empirical" in g.lower() for g in claim_res.gaps)


def test_standard_20_3_claim_supported_only_by_citation_count_not_verified(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 3: A claim supported only by high citation count of a paper cannot be verified."""
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Attention is all you need for any sequence modeling task.",
        status="proposed",
    )
    source_domain = LiteratureSource(
        research_run_id=test_run.id,
        provider="semantic_scholar",
        external_id="s2:12345",
        title="Attention Is All You Need",
        citation_count=120000,  # 120,000 citations
    )
    source = source_domain.to_persistence()
    db_session.add_all([claim, source])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=source.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
        metadata={"citation_count": 120000},
    )

    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(test_run.id)
    assert not report.is_passed
    assert any("missing empirical measurement" in g.lower() for g in report.claims_verified[0].gaps)


def test_standard_20_4_claim_supported_only_by_venue_not_verified(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 4: A claim supported only by publication venue prestige cannot be verified."""
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Deep learning guarantees zero generalization gap.",
        status="proposed",
    )
    source_domain = LiteratureSource(
        research_run_id=test_run.id,
        provider="openalex",
        external_id="W9999",
        title="Prestige Journal Paper",
        raw_metadata={"host_venue": "Nature Machine Intelligence"},
    )
    source = source_domain.to_persistence()
    db_session.add_all([claim, source])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=source.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
    )

    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(test_run.id)
    assert not report.is_passed


def test_standard_20_5_claim_numerical_value_differs_from_source(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 5: Explicit test distinguishing claim value from literature reported value."""
    # Literature reports 82.4%, but claim asserts 95.0%
    source_domain = LiteratureSource(
        research_run_id=test_run.id,
        provider="arxiv",
        external_id="arxiv:2105.1234",
        title="Benchmark Evaluation",
        abstract="We achieve 82.4% accuracy on ImageNet-1k.",
    )
    source = source_domain.to_persistence()
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Literature proves our model achieves 95.0% accuracy on ImageNet-1k.",
        status="proposed",
    )
    db_session.add_all([source, claim])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    # Linking literature citation while recording value disparity in metadata
    link = graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=source.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
        metadata={"claimed_val": 95.0, "source_val": 82.4, "disparity": True},
    )
    assert link.relationship_type == EvidenceRelationType.CITES
    assert link.metadata["disparity"] is True

    # Invalid semantic relationship between claim and literature must be rejected
    with pytest.raises(InvalidRelationError):
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=claim.id,
            target_type=EvidenceNodeType.LITERATURE_SOURCE,
            target_id=source.id,
            relationship_type=EvidenceRelationType.USES_CODE,  # Invalid relation
            research_run_id=test_run.id,
        )


def test_standard_20_6_claim_stronger_than_source_wording(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 6: Claim over-extending weak or qualified source wording."""
    source_domain = LiteratureSource(
        research_run_id=test_run.id,
        provider="arxiv",
        external_id="arxiv:2001.9999",
        title="Preliminary Explorations",
        abstract="In small toy experiments, method X may slightly improve convergence under some conditions.",
    )
    source = source_domain.to_persistence()
    # Claim asserting universal certainty
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Method X unconditionally guarantees 10x faster convergence in all regimes.",
        status="proposed",
    )
    db_session.add_all([source, claim])
    db_session.commit()

    # Mechanical verification check
    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(test_run.id)
    # Overextended claim cannot pass verifier
    assert not report.is_passed


def test_standard_20_7_conflicting_metadata_across_providers() -> None:
    """Scenario 7: Source with conflicting metadata across different providers preserves provenance."""
    # OpenAlex record says year 2021, title has subtitle
    src_openalex = LiteratureSource(
        research_run_id="run_1",
        provider="openalex",
        external_id="W2999",
        title="Scaling Transformers: A Comprehensive Study",
        year=2021,
    )
    # Semantic Scholar record says year 2020, title lacks subtitle
    src_s2 = LiteratureSource(
        research_run_id="run_1",
        provider="semantic_scholar",
        external_id="s2:9999",
        title="Scaling Transformers",
        year=2020,
    )

    # Invariant: Provider identities and external IDs are strictly preserved and do not collide
    assert src_openalex.provider == "openalex"
    assert src_s2.provider == "semantic_scholar"
    assert src_openalex.id != src_s2.id
    assert src_openalex.year != src_s2.year


def test_standard_20_8_two_sources_with_contradictory_results(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 8: Two literature sources reporting opposite findings are preserved in the evidence graph."""
    source_pro = LiteratureSource(
        research_run_id=test_run.id,
        provider="openalex",
        external_id="W111",
        title="Dropout Improves Small Dataset Generalization",
        abstract="We observe +4% accuracy improvement with dropout.",
    ).to_persistence()
    source_anti = LiteratureSource(
        research_run_id=test_run.id,
        provider="arxiv",
        external_id="arxiv:1802.2222",
        title="Dropout Degrades Modern Vision Architectures",
        abstract="We observe consistent accuracy degradation with dropout.",
    ).to_persistence()
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Dropout effects on transformer generalization.",
        status="investigating",
    )
    db_session.add_all([source_pro, source_anti, claim])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    # Link both sources with metadata capturing contradictory findings
    link_pro = graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=source_pro.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
        metadata={"finding": "improves"},
    )
    link_anti = graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=source_anti.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
        metadata={"finding": "degrades"},
    )

    assert link_pro.metadata["finding"] == "improves"
    assert link_anti.metadata["finding"] == "degrades"
    # Graph remains a valid acyclic structure without database constraint failure
    links = graph.get_links_for_run(test_run.id)
    assert len(links) == 2


def test_standard_20_9_llm_citation_to_nonexistent_source(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 9: Hallucinated / nonexistent literature source ID cannot be linked in evidence graph."""
    claim = ClaimModel(
        research_run_id=test_run.id,
        text="Claim attempting to cite a fabricated paper.",
        status="proposed",
    )
    db_session.add(claim)
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    fake_source_id = "lit_hallucinated_source_9999"

    with pytest.raises(NodeNotFoundError) as exc:
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=claim.id,
            target_type=EvidenceNodeType.LITERATURE_SOURCE,
            target_id=fake_source_id,
            relationship_type=EvidenceRelationType.CITES,
            research_run_id=test_run.id,
        )
    assert fake_source_id in str(exc.value)


def test_standard_20_10_llm_citation_to_unrelated_source(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 10: Irrelevant paper citation does not grant empirical verification to a claim."""
    biology_source = LiteratureSourceModel(
        research_run_id=test_run.id,
        provider="openalex",
        external_id="W_BIO_1",
        title="Mitochondrial respiration in yeast colonies",
        abstract="Cellular respiration study in biology.",
    )
    physics_claim = ClaimModel(
        research_run_id=test_run.id,
        text="Quantum entanglement verified in superconducting qubits.",
        status="proposed",
    )
    db_session.add_all([biology_source, physics_claim])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=physics_claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=biology_source.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
    )

    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(test_run.id)
    # Mere citation link to unrelated paper cannot verify the physics claim
    assert not report.is_passed
    assert not report.claims_verified[0].is_valid


def test_standard_20_11_source_content_immutability(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 11: LiteratureSource domain model is frozen and immutable after instantiation."""
    source = LiteratureSource(
        research_run_id=test_run.id,
        provider="arxiv",
        external_id="arxiv:1234.5678",
        title="Immutable Scholarly Record",
        abstract="Original abstract text.",
    )
    # Attempting to mutate title raises ValidationError / TypeError
    with pytest.raises((ValidationError, TypeError)):
        source.title = "Mutated Title"  # type: ignore

    with pytest.raises((ValidationError, TypeError)):
        source.abstract = "Mutated Abstract"  # type: ignore


def test_standard_20_12_source_from_another_research_run(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 12: Source from Run A cannot be cited in Run B (Cross-Run Isolation)."""
    run_a = test_run
    run_b = ResearchRunModel(
        id=f"run_ev_{uuid.uuid4().hex[:8]}",
        title="Isolated Run B",
        research_question="Isolation check",
        status="ANALYSIS",
    )
    db_session.add(run_b)
    db_session.commit()

    source_a = LiteratureSourceModel(
        research_run_id=run_a.id,
        provider="arxiv",
        external_id="arxiv:source_a",
        title="Paper for Run A",
    )
    claim_b = ClaimModel(
        research_run_id=run_b.id,
        text="Claim in Run B trying to cite Run A source.",
        status="proposed",
    )
    db_session.add_all([source_a, claim_b])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    with pytest.raises(CrossRunEvidenceError) as exc:
        graph.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=claim_b.id,
            target_type=EvidenceNodeType.LITERATURE_SOURCE,
            target_id=source_a.id,
            relationship_type=EvidenceRelationType.CITES,
            research_run_id=run_b.id,
        )
    assert "Cross-run evidence" in str(exc.value)


def test_standard_20_13_source_with_missing_retrieval_representation(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 13: Source with missing retrieval representation handles None abstract gracefully."""
    source = LiteratureSource(
        research_run_id=test_run.id,
        provider="semantic_scholar",
        external_id="s2:no_abstract_1",
        title="Title Only Index",
        abstract="",  # Missing or unavailable abstract
    )
    assert source.abstract == ""
    # Model can be persisted without crashing
    db_model = source.to_persistence()
    db_session.add(db_model)
    db_session.commit()

    loaded = db_session.get(LiteratureSourceModel, source.id)
    assert loaded is not None
    assert loaded.abstract == ""


def test_standard_20_14_metadata_vs_content_evidence() -> None:
    """Scenario 14: Distinguish between metadata evidence (citation count, venue) and content evidence."""
    source = LiteratureSource(
        research_run_id="run_1",
        provider="semantic_scholar",
        external_id="s2:meta_paper_1",
        title="Transformer Meta Analysis",
        citation_count=450,
        year=2023,
        abstract="Empirical evaluation of learning rates across 50 models.",
        raw_metadata={"journal": "JMLR", "open_access": True},
    )

    # Invariant: Metadata attributes are distinct from content text
    assert source.citation_count == 450
    assert source.year == 2023
    assert "learning rates" in source.abstract
    # Metadata is not confused with abstract body
    assert "JMLR" not in source.abstract
    assert source.raw_metadata["journal"] == "JMLR"


def test_standard_20_15_literature_result_cannot_be_promoted_to_verified(
    db_session: Session, test_run: ResearchRunModel
) -> None:
    """Scenario 15: Proves that a claim marked 'verified' that only links to literature fails formal verification."""
    # Create claim incorrectly set to verified in persistence
    fraudulent_claim = ClaimModel(
        research_run_id=test_run.id,
        text="A published paper said method X works, so our claim is verified.",
        status=ClaimStatus.VERIFIED.value,
    )
    paper = LiteratureSourceModel(
        research_run_id=test_run.id,
        provider="arxiv",
        external_id="arxiv:2303.1111",
        title="Groundbreaking Method X",
        abstract="We demonstrate superior convergence.",
    )
    db_session.add_all([fraudulent_claim, paper])
    db_session.commit()

    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=fraudulent_claim.id,
        target_type=EvidenceNodeType.LITERATURE_SOURCE,
        target_id=paper.id,
        relationship_type=EvidenceRelationType.CITES,
        research_run_id=test_run.id,
    )

    verifier = ResearchVerifier(session=db_session)
    report = verifier.verify_run(test_run.id)

    # The formal verifier MUST reject this claim because literature != mechanical verification
    assert not report.is_passed
    assert report.status == VerificationStatus.FAIL
    claim_audit = report.claims_verified[0]
    assert not claim_audit.is_valid
    assert not claim_audit.is_lineage_intact
    assert any("unsupported_claim" in err.lower() for err in report.errors)


# ==============================================================================
# Evidence Standard 19: Security and Evidence are Orthogonal
# ==============================================================================


def test_standard_19_security_and_evidence_are_orthogonal() -> None:
    """Prove that security risk classification does not alter scientific relevance and vice versa."""
    detector = InjectionDetector(fail_closed_on_high_risk=False)

    # Case 1: SAFE + SCIENTIFICALLY RELEVANT
    safe_paper = "Empirical study on transformer attention head pruning."
    scan_safe = detector.scan(safe_paper)
    assert scan_safe.risk_level == InjectionRiskLevel.SAFE
    assert not scan_safe.is_suspicious

    # Case 2: SUSPICIOUS / HIGH-RISK + SCIENTIFICALLY RELEVANT
    # Academic research paper specifically analyzing LLM instruction override defenses
    security_paper = (
        "We evaluate attacks where an adversary says: Ignore all previous instructions. "
        "We test against 10 modern defense frameworks."
    )
    scan_sec = detector.scan(security_paper)
    assert scan_sec.is_suspicious is True
    # The paper is recognized as discussing an attack vector, but its text is preserved
    assert scan_sec.sanitized_text != ""
    assert "We evaluate attacks" in scan_sec.sanitized_text

    # Case 3: LiteratureSource cannot execute regardless of security status
    safe_source = LiteratureSource(
        research_run_id="run_1",
        provider="arxiv",
        external_id="arxiv:safe_1",
        title="Safe Paper",
    )
    assert_literature_cannot_execute(safe_source)
