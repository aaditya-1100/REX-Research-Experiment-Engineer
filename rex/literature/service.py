"""REX Literature Subsystem Service (REX-028, REX-029, REX-030, REX-031, REX-032).

Coordinates scholarly literature discovery, provider dispatch, persistence, deduplication,
cross-run isolation, evidence graph citation linking, and quarantined prompt assembly.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from rex.config.settings import LiteratureSettings
from rex.domain.models import (
    EvidenceLink,
    EvidenceNodeType,
    EvidenceRelationType,
    LiteratureSource,
)
from rex.evidence.graph import CrossRunEvidenceError, EvidenceGraphService, NodeNotFoundError
from rex.literature.arxiv import ArXivProvider
from rex.literature.base import (
    InvalidQueryError,
    LiteratureProvider,
    LiteratureProviderError,
)
from rex.literature.models import (
    LiteratureSearchRequest,
    LiteratureSearchResult,
)
from rex.literature.openalex import OpenAlexProvider
from rex.literature.semantic_scholar import SemanticScholarProvider
from rex.literature.trust import (
    InjectionDetector,
    assert_literature_cannot_execute,
    build_literature_prompt_context,
)
from rex.observability.events import (
    ActorType,
    EventSink,
    EventType,
    create_event,
    emit_event,
)
from rex.persistence.models import ResearchRunModel
from rex.persistence.repositories import LiteratureSourceRepository

logger = logging.getLogger(__name__)


class LiteratureService:
    """Core service managing scholarly literature providers, storage, evidence linking, and security boundaries."""

    def __init__(
        self,
        session: Session,
        settings: LiteratureSettings | None = None,
        providers: Mapping[str, LiteratureProvider] | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or LiteratureSettings()
        self.event_sink = event_sink
        self.repository = LiteratureSourceRepository(session)
        self.evidence_service = EvidenceGraphService(session)
        self.detector = InjectionDetector(fail_closed_on_high_risk=False)

        # Register providers
        self._providers: dict[str, LiteratureProvider] = {}
        if providers:
            self._providers.update(providers)
        else:
            self._providers["openalex"] = OpenAlexProvider(self.settings)
            self._providers["semantic_scholar"] = SemanticScholarProvider(self.settings)
            self._providers["arxiv"] = ArXivProvider(self.settings)

    def register_provider(self, name: str, provider: LiteratureProvider) -> None:
        """Register a custom or test literature provider adapter."""
        self._providers[name.lower().strip()] = provider

    def get_provider(self, name: str) -> LiteratureProvider:
        """Retrieve a registered provider by name."""
        norm_name = name.lower().strip()
        provider = self._providers.get(norm_name)
        if provider is None:
            available = ", ".join(self._providers.keys())
            raise LiteratureProviderError(
                f"Unknown literature provider '{name}'. Available: {available}",
                provider=name,
            )
        return provider

    def _validate_run_exists(self, research_run_id: str) -> None:
        """Ensure the target research run exists in persistence."""
        if not research_run_id or not research_run_id.strip():
            raise InvalidQueryError("research_run_id must be provided and non-empty.")
        run = self.session.get(ResearchRunModel, research_run_id)
        if run is None:
            raise NodeNotFoundError(
                f"Research run '{research_run_id}' does not exist in persistence."
            )

    def search(
        self,
        provider_name: str,
        request: LiteratureSearchRequest,
        research_run_id: str,
        auto_persist: bool = True,
    ) -> LiteratureSearchResult:
        """Search scholarly publications via provider and store results in run evidence plane."""
        self._validate_run_exists(research_run_id)
        provider = self.get_provider(provider_name)

        result = provider.search(request, research_run_id=research_run_id)

        persisted_sources: list[LiteratureSource] = []
        for source in result.sources:
            # Enforce execution barrier
            assert_literature_cannot_execute(source)

            # Audit scan for prompt injection attempts
            scan_title = self.detector.scan(source.title)
            scan_abstract = self.detector.scan(source.abstract)

            if scan_title.is_suspicious or scan_abstract.is_suspicious:
                threats = list(scan_title.matched_patterns) + list(scan_abstract.matched_patterns)
                event = create_event(
                    event_type=EventType.LITERATURE_INJECTION_DETECTED,
                    actor=ActorType.LITERATURE_AGENT,
                    research_run_id=research_run_id,
                    payload={
                        "provider": provider.provider_name,
                        "external_id": source.external_id,
                        "title": source.title[:100],
                        "threat_count": len(threats),
                        "threats": threats,
                    },
                )
                emit_event(event, self.event_sink)

            if auto_persist:
                persisted = self._persist_source_if_new(source, research_run_id)
                persisted_sources.append(persisted)
            else:
                persisted_sources.append(source)

        # Emit literature searched event
        search_event = create_event(
            event_type=EventType.LITERATURE_SEARCHED,
            actor=ActorType.LITERATURE_AGENT,
            research_run_id=research_run_id,
            payload={
                "provider": provider.provider_name,
                "query": request.query,
                "results_count": len(persisted_sources),
                "total_results": result.total_results,
            },
        )
        emit_event(search_event, self.event_sink)

        return LiteratureSearchResult(
            query=result.query,
            provider=result.provider,
            sources=tuple(persisted_sources),
            total_results=result.total_results,
            next_cursor=result.next_cursor,
            retrieved_at=result.retrieved_at,
        )

    def get_by_id(
        self,
        provider_name: str,
        external_id: str,
        research_run_id: str,
        auto_persist: bool = True,
    ) -> LiteratureSource | None:
        """Fetch a specific publication by ID and store in run evidence plane."""
        self._validate_run_exists(research_run_id)
        provider = self.get_provider(provider_name)

        source = provider.get_by_id(external_id, research_run_id=research_run_id)
        if source is None:
            return None

        assert_literature_cannot_execute(source)

        scan = self.detector.scan(f"{source.title} {source.abstract}")
        if scan.is_suspicious:
            event = create_event(
                event_type=EventType.LITERATURE_INJECTION_DETECTED,
                actor=ActorType.LITERATURE_AGENT,
                research_run_id=research_run_id,
                payload={
                    "provider": provider.provider_name,
                    "external_id": source.external_id,
                    "threats": list(scan.matched_patterns),
                },
            )
            emit_event(event, self.event_sink)

        if auto_persist:
            return self._persist_source_if_new(source, research_run_id)
        return source

    def _persist_source_if_new(
        self, source: LiteratureSource, research_run_id: str
    ) -> LiteratureSource:
        """Check if source exists for this run; if not, persist it."""
        existing = self.repository.get_by_external_id(
            research_run_id=research_run_id,
            provider=source.provider,
            external_id=source.external_id,
        )
        if existing is not None:
            return LiteratureSource.from_persistence(existing)

        # Set correct research_run_id
        if source.research_run_id != research_run_id:
            source = source.model_copy(update={"research_run_id": research_run_id})

        model = source.to_persistence()
        saved = self.repository.create(model)

        # Emit retrieval event
        retrieved_event = create_event(
            event_type=EventType.LITERATURE_RETRIEVED,
            actor=ActorType.LITERATURE_AGENT,
            research_run_id=research_run_id,
            payload={
                "source_id": saved.id,
                "provider": saved.provider,
                "external_id": saved.external_id,
                "title": saved.title[:120],
                "year": saved.year,
            },
        )
        emit_event(retrieved_event, self.event_sink)

        return LiteratureSource.from_persistence(saved)

    def get_source(self, source_id: str, research_run_id: str) -> LiteratureSource | None:
        """Retrieve a stored literature source by ID, strictly enforcing research run isolation."""
        model = self.repository.get_by_id(source_id)
        if model is None:
            return None
        if model.research_run_id != research_run_id:
            raise CrossRunEvidenceError(
                f"Cross-run access violation: literature source '{source_id}' belongs to "
                f"run '{model.research_run_id}', not '{research_run_id}'."
            )
        return LiteratureSource.from_persistence(model)

    def list_sources(self, research_run_id: str) -> list[LiteratureSource]:
        """List all persisted literature sources associated with a research run."""
        models = self.repository.list_by_run(research_run_id)
        return [LiteratureSource.from_persistence(m) for m in models]

    def link_to_claim(
        self,
        claim_id: str,
        source_id: str,
        research_run_id: str,
        relationship_type: EvidenceRelationType = EvidenceRelationType.CITES,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceLink:
        """Link a scientific claim to a literature source in the evidence graph (REX-023/REX-028).

        Enforces run isolation, cycle prevention, and typed relationship semantics.
        """
        # Validate source belongs to this run
        source = self.get_source(source_id, research_run_id)
        if source is None:
            raise NodeNotFoundError(
                f"Literature source '{source_id}' not found for run '{research_run_id}'."
            )

        link = self.evidence_service.create_link(
            source_type=EvidenceNodeType.CLAIM,
            source_id=claim_id,
            target_type=EvidenceNodeType.LITERATURE_SOURCE,
            target_id=source_id,
            relationship_type=relationship_type,
            research_run_id=research_run_id,
            created_by="literature_service",
            metadata=metadata or {},
        )
        return link

    def build_prompt_context(
        self,
        research_run_id: str,
        source_ids: Sequence[str] | None = None,
        max_total_chars: int = 15000,
    ) -> str:
        """Build the quarantined, tamper-evident prompt context block for an LLM agent."""
        all_sources = self.list_sources(research_run_id)
        if source_ids is not None:
            target_ids = set(source_ids)
            filtered = [s for s in all_sources if s.id in target_ids]
        else:
            filtered = all_sources

        return build_literature_prompt_context(sources=filtered, max_total_chars=max_total_chars)


__all__ = ["LiteratureService"]
