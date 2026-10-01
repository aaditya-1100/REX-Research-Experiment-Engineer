"""Unit tests for Research Critic Agent (REX-033).

Tests:
- Critic initialization and role
- Critique generation from evidence context (hypotheses, experiments, results, claims)
- Finding classifications and severities
- Hallucination quarantine: unresolved entity IDs are masked and recorded as weaknesses
- Persistence of critiques and events in repository
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.agents.critic import ResearchCriticAgent
from rex.domain.models import (
    CritiqueCategory,
    CritiqueSeverity,
    ExpectedDirection,
)
from rex.llm.base import LLMProvider
from rex.llm.models import LLMRequest, LLMResponse
from rex.observability.events import EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.persistence.repositories import CritiqueRepository


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def populated_run(db_session: Session) -> str:
    """Create a research run with populated hypothesis, experiment, execution, and results."""
    run = ResearchRunModel(
        title="Critic Test Run",
        research_question="Does feature selection improve random forest classification?",
    )
    db_session.add(run)
    db_session.flush()

    hyp = HypothesisModel(
        research_run_id=run.id,
        statement="Feature selection improves accuracy by removing noisy predictors.",
        rationale="Eliminating irrelevant features reduces model variance.",
        expected_direction=ExpectedDirection.INCREASE.value,
        falsification_condition="Test accuracy decreases or remains identical.",
    )
    db_session.add(hyp)
    db_session.flush()

    exp = ExperimentModel(
        research_run_id=run.id,
        hypothesis_id=hyp.id,
        objective="Random forest with mutual information feature selection",
        specification_json={"name": "exp_rf_feature_selection"},
    )
    db_session.add(exp)
    db_session.flush()

    execution = ExecutionModel(
        experiment_id=exp.id,
        status="completed",
        exit_code=0,
    )
    db_session.add(execution)
    db_session.flush()

    res = ResultModel(
        execution_id=execution.id,
        metric_name="accuracy",
        metric_value=0.895,
        metric_unit="ratio",
    )
    db_session.add(res)
    db_session.commit()
    return run.id


class MockCriticLLMProvider(LLMProvider):
    """Deterministic LLM mock responding with structured critique proposals."""

    def __init__(self, response_json_str: str) -> None:
        self.response_json_str = response_json_str

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            text=self.response_json_str,
            provider="mock",
            model="mock-critic-v1",
        )

    def count_tokens(self, text: str) -> int:
        return len(text.split())


@pytest.mark.unit
def test_critic_default_heuristic_without_llm(db_session: Session, populated_run: str) -> None:
    """Critic should produce a valid critique from LLM provider."""
    mock_json = """{
        "summary": "Evaluated experiments on feature selection; results show positive improvement.",
        "strengths": ["Clear baseline declared", "Hypothesis falsification condition stated"],
        "weaknesses": ["Sample size is limited to one dataset"],
        "contradictions": [],
        "unresolved_questions": ["Generalization to deep learning models?"],
        "methodological_concerns": [],
        "findings": [
            {
                "category": "sample_size",
                "severity": "low",
                "description": "Evaluated on single benchmark.",
                "epistemic_status": "observed",
                "evidence_refs": [],
                "recommendation": "Evaluate across additional datasets."
            }
        ],
        "recommended_action": "refine",
        "recommended_action_rationale": "Refine with additional datasets to confirm robustness."
    }"""
    llm = MockCriticLLMProvider(mock_json)
    sink = InMemoryEventSink()
    critic = ResearchCriticAgent(provider=llm, event_sink=sink)

    critique = critic.critique_research(
        research_run_id=populated_run,
        session=db_session,
        iteration=1,
    )

    assert critique.research_run_id == populated_run
    assert critique.iteration == 1
    assert len(critique.strengths) > 0
    assert critique.recommended_action == "refine"

    # Verify critique was persisted
    repo = CritiqueRepository(db_session)
    stored = repo.get_by_id(critique.id)
    assert stored is not None
    assert stored.research_run_id == populated_run

    # Verify event emission
    events = sink.get_by_type(EventType.CRITIQUE_COMPLETED)
    assert len(events) == 1
    assert events[0].payload["critique_id"] == critique.id


@pytest.mark.unit
def test_critic_hallucination_quarantine(db_session: Session, populated_run: str) -> None:
    """Critic must quarantine hallucinated entity IDs that do not exist in the active run."""
    mock_json = """{
        "summary": "Evaluated experiments on feature selection.",
        "strengths": ["Clear baseline declared"],
        "weaknesses": ["Sample size may be small"],
        "contradictions": [],
        "unresolved_questions": ["What about non-linear feature interactions?"],
        "methodological_concerns": ["Only tested on single dataset"],
        "findings": [
            {
                "category": "methodology",
                "severity": "high",
                "description": "Experiment exp_HALLUCINATED_DOES_NOT_EXIST lacks hyperparameter search.",
                "epistemic_status": "inferred",
                "evidence_refs": ["exp_HALLUCINATED_DOES_NOT_EXIST"],
                "recommendation": "Test with broader parameter grid."
            }
        ],
        "recommended_action": "refine",
        "recommended_action_rationale": "Refine experiment with hyperparameter tuning."
    }"""
    llm = MockCriticLLMProvider(mock_json)
    sink = InMemoryEventSink()
    critic = ResearchCriticAgent(provider=llm, event_sink=sink)

    critique = critic.critique_research(
        research_run_id=populated_run,
        session=db_session,
        iteration=1,
    )

    assert len(critique.findings) == 1
    finding = critique.findings[0]
    # The hallucinated ID in evidence_refs must be quarantined
    assert any("[UNRESOLVED_ENTITY" in ref for ref in finding.evidence_refs)
    assert finding.severity == CritiqueSeverity.HIGH

    # Weaknesses should record the quarantined hallucination
    assert any("exp_HALLUCINATED_DOES_NOT_EXIST" in w for w in critique.weaknesses)


@pytest.mark.unit
def test_critic_critical_flaw_detection(db_session: Session, populated_run: str) -> None:
    """Critic properly identifies and classifies critical methodological flaws."""
    mock_json = """{
        "summary": "Fatal target leakage detected in feature selection step.",
        "strengths": [],
        "weaknesses": ["Data leakage before train/test split"],
        "contradictions": [],
        "unresolved_questions": [],
        "methodological_concerns": ["Features selected using test labels"],
        "findings": [
            {
                "category": "leakage",
                "severity": "critical",
                "description": "Information from test split leaked into mutual information selector.",
                "epistemic_status": "observed",
                "evidence_refs": [],
                "recommendation": "Fit selector strictly on train split."
            }
        ],
        "recommended_action": "refine",
        "recommended_action_rationale": "Must refine pipeline to eliminate data leakage."
    }"""
    llm = MockCriticLLMProvider(mock_json)
    critic = ResearchCriticAgent(provider=llm)

    critique = critic.critique_research(
        research_run_id=populated_run,
        session=db_session,
        iteration=2,
    )

    assert critique.has_critical_findings
    critical_findings = [f for f in critique.findings if f.severity == CritiqueSeverity.CRITICAL]
    assert len(critical_findings) == 1
    assert critical_findings[0].category == CritiqueCategory.LEAKAGE
    assert "leak" in critical_findings[0].description.lower()
