"""REX Evidence & Verification Plane (Batch 4: REX-023 through REX-027).

Provides formal empirical traceability from scientific claims down to experiments,
cryptographic content hashing, read-only research verification, and non-destructive
reproducibility workflows.
"""

from rex.evidence.claims import (
    ClaimError,
    ClaimNotFoundError,
    ClaimService,
    InvalidClaimStatusTransitionError,
    UnauthorizedClaimError,
    UnsupportedClaimError,
)
from rex.evidence.graph import (
    ClaimLineage,
    CrossRunEvidenceError,
    EvidenceCycleError,
    EvidenceGraphError,
    EvidenceGraphService,
    InvalidRelationError,
    NodeNotFoundError,
)
from rex.evidence.hashing import (
    HashVerificationResult,
    canonical_json_dumps,
    canonical_json_hash,
    compute_bytes_hash,
    compute_file_hash,
    verify_artifact_hash,
)
from rex.evidence.reproduce import (
    ExperimentReproducer,
    MetricComparison,
    ReproducibilityAssessment,
    ReproducibilityStatus,
    ReproductionOutcome,
    ReproductionReport,
)
from rex.evidence.self_deception import (
    ScientificSelfDeceptionDetector,
    SelfDeceptionAuditReport,
    SelfDeceptionFinding,
    SelfDeceptionType,
)
from rex.evidence.verifier import (
    AnalysisRecomputationResult,
    ClaimVerificationResult,
    ResearchVerifier,
    VerificationReport,
    VerificationStatus,
)

__all__ = [
    "AnalysisRecomputationResult",
    "ClaimError",
    "ClaimLineage",
    "ClaimNotFoundError",
    "ClaimService",
    "ClaimVerificationResult",
    "CrossRunEvidenceError",
    "EvidenceCycleError",
    "EvidenceGraphError",
    "EvidenceGraphService",
    "ExperimentReproducer",
    "HashVerificationResult",
    "InvalidClaimStatusTransitionError",
    "InvalidRelationError",
    "MetricComparison",
    "NodeNotFoundError",
    "ReproducibilityAssessment",
    "ReproducibilityStatus",
    "ReproductionOutcome",
    "ReproductionReport",
    "ResearchVerifier",
    "ScientificSelfDeceptionDetector",
    "SelfDeceptionAuditReport",
    "SelfDeceptionFinding",
    "SelfDeceptionType",
    "UnauthorizedClaimError",
    "UnsupportedClaimError",
    "VerificationReport",
    "VerificationStatus",
    "canonical_json_dumps",
    "canonical_json_hash",
    "compute_bytes_hash",
    "compute_file_hash",
    "verify_artifact_hash",
]
