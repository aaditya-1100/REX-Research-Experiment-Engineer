"""REX Scientific Reasoning Agents (REX-013, REX-014, REX-015, REX-016).

Exposes the reasoning-plane intelligence layer:
- InvestigatorAgent (REX-013): Scopes research questions into formal ResearchContext
- HypothesisAgent (REX-014): Generates falsifiable hypotheses with expected direction
- ExperimentDesignerAgent (REX-015): Formulates rigorous ExperimentSpecifications
- CodingAgent (REX-016): Implements specifications into immutable GeneratedExperiment code packages
"""

from rex.agents.base import BaseAgent
from rex.agents.code_aligner import (
    AlignmentIssue,
    AlignmentIssueType,
    CodeAlignmentReport,
    MethodCodeAligner,
)
from rex.agents.coding import (
    CodingAgent,
    compute_canonical_code_hash,
    create_execution_request_from_generated,
    validate_code_proposal,
)
from rex.agents.critic import ResearchCriticAgent
from rex.agents.experiment_designer import ExperimentDesignerAgent
from rex.agents.flaw_detector import (
    DesignFlaw,
    DesignFlawReport,
    DesignFlawType,
    ExperimentDesignFlawDetector,
    FlawSeverity,
)
from rex.agents.hypothesis import HypothesisAgent
from rex.agents.hypothesis_validator import HypothesisValidationResult, HypothesisValidator
from rex.agents.investigator import InvestigatorAgent

__all__ = [
    "AlignmentIssue",
    "AlignmentIssueType",
    "BaseAgent",
    "CodeAlignmentReport",
    "CodingAgent",
    "DesignFlaw",
    "DesignFlawReport",
    "DesignFlawType",
    "ExperimentDesignFlawDetector",
    "ExperimentDesignerAgent",
    "FlawSeverity",
    "HypothesisAgent",
    "HypothesisValidationResult",
    "HypothesisValidator",
    "InvestigatorAgent",
    "MethodCodeAligner",
    "ResearchCriticAgent",
    "compute_canonical_code_hash",
    "create_execution_request_from_generated",
    "validate_code_proposal",
]
