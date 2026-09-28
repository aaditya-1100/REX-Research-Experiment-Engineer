"""REX Persistence Module (REX-004).

Exports database engine management, declarative models, and repository interfaces.
"""

from rex.persistence.database import (
    Base,
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
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
from rex.persistence.repositories import (
    AnalysisRepository,
    ArtifactRepository,
    BaseRepository,
    ClaimRepository,
    DatabaseEventSink,
    EventRepository,
    ExecutionRepository,
    ExperimentRepository,
    HypothesisRepository,
    LiteratureSourceRepository,
    ResearchRunRepository,
    ResultRepository,
)

__all__ = [
    "AnalysisModel",
    "AnalysisRepository",
    "ArtifactModel",
    "ArtifactRepository",
    "Base",
    "BaseRepository",
    "ClaimModel",
    "ClaimRepository",
    "DatabaseEventSink",
    "EventModel",
    "EventRepository",
    "EvidenceLinkModel",
    "ExecutionModel",
    "ExecutionRepository",
    "ExperimentModel",
    "ExperimentRepository",
    "HypothesisModel",
    "HypothesisRepository",
    "LiteratureSourceModel",
    "LiteratureSourceRepository",
    "ResearchRunModel",
    "ResearchRunRepository",
    "ResultModel",
    "ResultRepository",
    "create_db_engine",
    "create_session_factory",
    "get_db_session",
    "init_db",
]
