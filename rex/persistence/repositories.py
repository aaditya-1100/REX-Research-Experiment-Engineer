"""REX Persistence Repositories (REX-004).

Provides repository abstractions for domain entities, supporting CRUD operations,
provenance graph linkages, and integration with the REX-003 structured event system.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from rex.observability.events import EventSink, ResearchEvent, create_event
from rex.persistence.database import get_db_session
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    ClaimModel,
    EventModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    LiteratureSourceModel,
    ResearchRunModel,
    ResultModel,
)


class BaseRepository:
    """Base repository providing database session management."""

    def __init__(self, session: Session) -> None:
        self.session = session


class ResearchRunRepository(BaseRepository):
    """Repository for managing top-level research investigations."""

    def create(self, run: ResearchRunModel) -> ResearchRunModel:
        self.session.add(run)
        self.session.flush()
        return run

    def get_by_id(self, run_id: str) -> ResearchRunModel | None:
        return self.session.get(ResearchRunModel, run_id)

    def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[ResearchRunModel]:
        stmt = (
            select(ResearchRunModel)
            .order_by(ResearchRunModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return self.session.scalars(stmt).all()

    def update_status(self, run_id: str, status: str) -> ResearchRunModel | None:
        run = self.get_by_id(run_id)
        if run is not None:
            run.status = status
            run.updated_at = datetime.now(UTC)
            self.session.flush()
        return run

    def delete(self, run_id: str) -> bool:
        run = self.get_by_id(run_id)
        if run is not None:
            self.session.delete(run)
            self.session.flush()
            return True
        return False


class HypothesisRepository(BaseRepository):
    """Repository for managing testable scientific hypotheses."""

    def create(self, hypothesis: HypothesisModel) -> HypothesisModel:
        self.session.add(hypothesis)
        self.session.flush()
        return hypothesis

    def get_by_id(self, hypothesis_id: str) -> HypothesisModel | None:
        return self.session.get(HypothesisModel, hypothesis_id)

    def list_by_run(self, research_run_id: str) -> Sequence[HypothesisModel]:
        stmt = (
            select(HypothesisModel)
            .where(HypothesisModel.research_run_id == research_run_id)
            .order_by(HypothesisModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def update_status(self, hypothesis_id: str, status: str) -> HypothesisModel | None:
        hyp = self.get_by_id(hypothesis_id)
        if hyp is not None:
            hyp.status = status
            self.session.flush()
        return hyp


class ExperimentRepository(BaseRepository):
    """Repository for managing experiment specifications and refinement hierarchies."""

    def create(self, experiment: ExperimentModel) -> ExperimentModel:
        self.session.add(experiment)
        self.session.flush()
        return experiment

    def get_by_id(self, experiment_id: str) -> ExperimentModel | None:
        return self.session.get(ExperimentModel, experiment_id)

    def list_by_run(self, research_run_id: str) -> Sequence[ExperimentModel]:
        stmt = (
            select(ExperimentModel)
            .where(ExperimentModel.research_run_id == research_run_id)
            .order_by(ExperimentModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def list_by_hypothesis(self, hypothesis_id: str) -> Sequence[ExperimentModel]:
        stmt = (
            select(ExperimentModel)
            .where(ExperimentModel.hypothesis_id == hypothesis_id)
            .order_by(ExperimentModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def list_by_parent(self, parent_experiment_id: str) -> Sequence[ExperimentModel]:
        stmt = (
            select(ExperimentModel)
            .where(ExperimentModel.parent_experiment_id == parent_experiment_id)
            .order_by(ExperimentModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def update_status(self, experiment_id: str, status: str) -> ExperimentModel | None:
        exp = self.get_by_id(experiment_id)
        if exp is not None:
            exp.status = status
            self.session.flush()
        return exp


class ExecutionRepository(BaseRepository):
    """Repository for managing concrete execution attempts and runtime telemetry."""

    def create(self, execution: ExecutionModel) -> ExecutionModel:
        self.session.add(execution)
        self.session.flush()
        return execution

    def get_by_id(self, execution_id: str) -> ExecutionModel | None:
        return self.session.get(ExecutionModel, execution_id)

    def list_by_experiment(self, experiment_id: str) -> Sequence[ExecutionModel]:
        stmt = (
            select(ExecutionModel)
            .where(ExecutionModel.experiment_id == experiment_id)
            .order_by(ExecutionModel.started_at.asc().nulls_first())
        )
        return self.session.scalars(stmt).all()

    def update_status(
        self,
        execution_id: str,
        status: str,
        exit_code: int | None = None,
        finished_at: datetime | None = None,
    ) -> ExecutionModel | None:
        execution = self.get_by_id(execution_id)
        if execution is not None:
            execution.status = status
            if exit_code is not None:
                execution.exit_code = exit_code
            if finished_at is not None:
                execution.finished_at = finished_at
            self.session.flush()
        return execution


class ResultRepository(BaseRepository):
    """Repository for managing raw experimental output metrics."""

    def create(self, result: ResultModel) -> ResultModel:
        self.session.add(result)
        self.session.flush()
        return result

    def create_batch(self, results: list[ResultModel]) -> list[ResultModel]:
        self.session.add_all(results)
        self.session.flush()
        return results

    def get_by_id(self, result_id: str) -> ResultModel | None:
        return self.session.get(ResultModel, result_id)

    def list_by_execution(self, execution_id: str) -> Sequence[ResultModel]:
        stmt = (
            select(ResultModel)
            .where(ResultModel.execution_id == execution_id)
            .order_by(ResultModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def list_by_metric(self, execution_id: str, metric_name: str) -> Sequence[ResultModel]:
        stmt = (
            select(ResultModel)
            .where(
                ResultModel.execution_id == execution_id,
                ResultModel.metric_name == metric_name,
            )
            .order_by(ResultModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()


class AnalysisRepository(BaseRepository):
    """Repository for statistical evaluations derived from experiment results."""

    def create(self, analysis: AnalysisModel) -> AnalysisModel:
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def get_by_id(self, analysis_id: str) -> AnalysisModel | None:
        return self.session.get(AnalysisModel, analysis_id)

    def list_by_run(self, research_run_id: str) -> Sequence[AnalysisModel]:
        stmt = (
            select(AnalysisModel)
            .where(AnalysisModel.research_run_id == research_run_id)
            .order_by(AnalysisModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()


class ArtifactRepository(BaseRepository):
    """Repository for filesystem artifact metadata tracking and integrity verification."""

    def create(self, artifact: ArtifactModel) -> ArtifactModel:
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def get_by_id(self, artifact_id: str) -> ArtifactModel | None:
        return self.session.get(ArtifactModel, artifact_id)

    def list_by_run(self, research_run_id: str) -> Sequence[ArtifactModel]:
        stmt = (
            select(ArtifactModel)
            .where(ArtifactModel.research_run_id == research_run_id)
            .order_by(ArtifactModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def list_by_execution(self, execution_id: str) -> Sequence[ArtifactModel]:
        stmt = (
            select(ArtifactModel)
            .where(ArtifactModel.execution_id == execution_id)
            .order_by(ArtifactModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()


class LiteratureSourceRepository(BaseRepository):
    """Repository for retrieved scholarly literature and citation records."""

    def create(self, source: LiteratureSourceModel) -> LiteratureSourceModel:
        self.session.add(source)
        self.session.flush()
        return source

    def get_by_id(self, source_id: str) -> LiteratureSourceModel | None:
        return self.session.get(LiteratureSourceModel, source_id)

    def list_by_run(self, research_run_id: str) -> Sequence[LiteratureSourceModel]:
        stmt = (
            select(LiteratureSourceModel)
            .where(LiteratureSourceModel.research_run_id == research_run_id)
            .order_by(LiteratureSourceModel.retrieved_at.asc())
        )
        return self.session.scalars(stmt).all()


class ClaimRepository(BaseRepository):
    """Repository for scientific claims and provenance evidence graph links."""

    def create(self, claim: ClaimModel) -> ClaimModel:
        self.session.add(claim)
        self.session.flush()
        return claim

    def get_by_id(self, claim_id: str) -> ClaimModel | None:
        return self.session.get(ClaimModel, claim_id)

    def list_by_run(self, research_run_id: str) -> Sequence[ClaimModel]:
        stmt = (
            select(ClaimModel)
            .where(ClaimModel.research_run_id == research_run_id)
            .order_by(ClaimModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()

    def update_status(self, claim_id: str, status: str) -> ClaimModel | None:
        claim = self.get_by_id(claim_id)
        if claim is not None:
            claim.status = status
            self.session.flush()
        return claim

    def add_evidence_link(
        self,
        claim_id: str,
        source_type: str,
        source_id: str,
        relationship_type: str = "supported_by",
        link_id: str | None = None,
    ) -> EvidenceLinkModel:
        link = EvidenceLinkModel(
            claim_id=claim_id,
            source_type=source_type,
            source_id=source_id,
            relationship_type=relationship_type,
        )
        if link_id is not None:
            link.id = link_id
        self.session.add(link)
        self.session.flush()
        return link

    def get_evidence_links(self, claim_id: str) -> Sequence[EvidenceLinkModel]:
        stmt = (
            select(EvidenceLinkModel)
            .where(EvidenceLinkModel.claim_id == claim_id)
            .order_by(EvidenceLinkModel.created_at.asc())
        )
        return self.session.scalars(stmt).all()


class EventRepository(BaseRepository):
    """Repository for historical research lifecycle audit events, bridging REX-003 and REX-004."""

    @staticmethod
    def to_event_model(event: ResearchEvent) -> EventModel:
        """Convert a REX-003 ResearchEvent into an EventModel for persistence."""
        event_dict = event.to_dict()
        payload = dict(event_dict.get("payload", {}))
        if event.experiment_id is not None:
            payload["_experiment_id"] = event.experiment_id
        if event.execution_id is not None:
            payload["_execution_id"] = event.execution_id

        return EventModel(
            id=event.event_id,
            research_run_id=event.research_run_id,
            event_type=(
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type)
            ),
            timestamp=event.timestamp,
            actor_type=(event.actor.value if hasattr(event.actor, "value") else str(event.actor)),
            actor_id="system",
            payload_json=payload,
        )

    @staticmethod
    def to_research_event(model: EventModel) -> ResearchEvent:
        """Convert an EventModel into an immutable REX-003 ResearchEvent."""
        payload = dict(model.payload_json or {})
        experiment_id = payload.pop("_experiment_id", None)
        execution_id = payload.pop("_execution_id", None)

        ts = model.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        return create_event(
            event_type=model.event_type,
            actor=model.actor_type,
            research_run_id=model.research_run_id,
            payload=payload,
            experiment_id=experiment_id,
            execution_id=execution_id,
            event_id=model.id,
            timestamp=ts,
        )

    def record_event(self, event: ResearchEvent | EventModel) -> EventModel:
        """Persist a research event (accepting either ResearchEvent or EventModel)."""
        if isinstance(event, ResearchEvent):
            model = self.to_event_model(event)
        else:
            model = event

        self.session.add(model)
        self.session.flush()
        return model

    def get_by_id(self, event_id: str) -> EventModel | None:
        return self.session.get(EventModel, event_id)

    def get_research_event(self, event_id: str) -> ResearchEvent | None:
        model = self.get_by_id(event_id)
        if model is None:
            return None
        return self.to_research_event(model)

    def list_by_run(
        self, research_run_id: str, limit: int = 100, offset: int = 0
    ) -> Sequence[EventModel]:
        stmt = (
            select(EventModel)
            .where(EventModel.research_run_id == research_run_id)
            .order_by(EventModel.timestamp.asc())
            .offset(offset)
            .limit(limit)
        )
        return self.session.scalars(stmt).all()

    def list_research_events_by_run(
        self, research_run_id: str, limit: int = 100, offset: int = 0
    ) -> list[ResearchEvent]:
        models = self.list_by_run(research_run_id, limit=limit, offset=offset)
        return [self.to_research_event(m) for m in models]


class DatabaseEventSink(EventSink):
    """Event sink implementation that persists ResearchEvents to the database."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def emit(self, event: ResearchEvent) -> None:
        with get_db_session(self.session_factory) as session:
            repo = EventRepository(session)
            repo.record_event(event)
