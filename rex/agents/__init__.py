"""REX Scientific Reasoning Agents (REX-013, REX-014, REX-015, REX-016).

Exposes the reasoning-plane intelligence layer:
- InvestigatorAgent (REX-013): Scopes research questions into formal ResearchContext
- HypothesisAgent (REX-014): Generates falsifiable hypotheses with expected direction
- ExperimentDesignerAgent (REX-015): Formulates rigorous ExperimentSpecifications
- CodingAgent (REX-016): Implements specifications into immutable GeneratedExperiment code packages
"""

from rex.agents.base import BaseAgent
from rex.agents.coding import (
    CodingAgent,
    compute_canonical_code_hash,
    create_execution_request_from_generated,
    validate_code_proposal,
)
from rex.agents.experiment_designer import ExperimentDesignerAgent
from rex.agents.hypothesis import HypothesisAgent
from rex.agents.investigator import InvestigatorAgent

__all__ = [
    "BaseAgent",
    "CodingAgent",
    "ExperimentDesignerAgent",
    "HypothesisAgent",
    "InvestigatorAgent",
    "compute_canonical_code_hash",
    "create_execution_request_from_generated",
    "validate_code_proposal",
]
