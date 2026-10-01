"""Unit tests for REX Literature Domain Models & Schemas (REX-028)."""

import pytest
from pydantic import ValidationError

from rex.domain.models import LiteratureSource
from rex.literature.models import (
    Author,
    LiteratureSearchRequest,
    LiteratureSearchResult,
    ProviderCapabilities,
)
from rex.persistence.models import LiteratureSourceModel


def test_author_model_validation() -> None:
    """Verify Author creation, validation, and immutability."""
    auth = Author(
        name="Alice Lovelace",
        affiliations=["University of Computing", "AI Institute"],
        external_ids={"orcid": "0000-0002-1825-0097"},
    )
    assert auth.name == "Alice Lovelace"
    assert auth.affiliations == ("University of Computing", "AI Institute")
    assert auth.external_ids["orcid"] == "0000-0002-1825-0097"

    # Frozen checks
    with pytest.raises(ValidationError):
        auth.name = "Bob"  # type: ignore

    # Empty name fails
    with pytest.raises(ValidationError):
        Author(name="   ")


def test_literature_search_request_validation() -> None:
    """Verify LiteratureSearchRequest parameter validation and bounds."""
    req = LiteratureSearchRequest(query="deep reinforcement learning", limit=25, offset=10)
    assert req.query == "deep reinforcement learning"
    assert req.limit == 25
    assert req.offset == 10

    # Empty query fails
    with pytest.raises(ValidationError):
        LiteratureSearchRequest(query="")

    # Limit bounds
    with pytest.raises(ValidationError):
        LiteratureSearchRequest(query="valid", limit=0)
    with pytest.raises(ValidationError):
        LiteratureSearchRequest(query="valid", limit=101)

    # Year validation
    req_year = LiteratureSearchRequest(query="valid", year_start=2020, year_end=2024)
    assert req_year.year_start == 2020
    assert req_year.year_end == 2024

    with pytest.raises(ValidationError):
        LiteratureSearchRequest(query="valid", year_start=2024, year_end=2020)


def test_literature_source_domain_and_persistence_roundtrip() -> None:
    """Verify LiteratureSource domain model maps deterministically to and from persistence."""
    source = LiteratureSource(
        research_run_id="run_test_123",
        provider="openalex",
        external_id="W123456789",
        title="Attention Is All You Need",
        authors=("Ashish Vaswani", "Noam Shazeer"),
        year=2017,
        abstract="The dominant sequence transduction models are based on complex recurrent...",
        url="https://arxiv.org/abs/1706.03762",
        citation_count=50000,
        doi="https://doi.org/10.48550/arXiv.1706.03762",
        raw_metadata={"openalex_id": "W123456789", "concepts": ["Transformer", "Self-Attention"]},
    )

    # Immutability
    with pytest.raises(ValidationError):
        source.title = "Modified"  # type: ignore

    # Persistence roundtrip
    model = source.to_persistence()
    assert isinstance(model, LiteratureSourceModel)
    assert model.id == source.id
    assert model.research_run_id == "run_test_123"
    assert model.provider == "openalex"
    assert model.external_id == "W123456789"
    assert model.title == "Attention Is All You Need"
    assert model.authors_json == ["Ashish Vaswani", "Noam Shazeer"]
    assert model.year == 2017
    assert model.abstract == source.abstract
    assert model.url == source.url
    assert model.raw_metadata_json["openalex_id"] == "W123456789"

    # From persistence
    restored = LiteratureSource.from_persistence(model)
    assert restored.id == source.id
    assert restored.research_run_id == source.research_run_id
    assert restored.provider == source.provider
    assert restored.external_id == source.external_id
    assert restored.title == source.title
    assert restored.authors == source.authors
    assert restored.year == source.year
    assert restored.abstract == source.abstract
    assert restored.citation_count == 50000
    assert restored.doi == "https://doi.org/10.48550/arXiv.1706.03762"


def test_literature_source_content_hash_determinism() -> None:
    """Verify that identical literature sources produce identical cryptographic content hashes."""
    s1 = LiteratureSource(
        research_run_id="run_a",
        provider="arxiv",
        external_id="2303.08774",
        title="GPT-4 Technical Report",
        authors=("OpenAI",),
        year=2023,
        abstract="We report the development of GPT-4...",
    )
    s2 = LiteratureSource(
        research_run_id="run_b",  # Different run ID does NOT affect content hash
        provider="arxiv",
        external_id="2303.08774",
        title="GPT-4 Technical Report",
        authors=("OpenAI",),
        year=2023,
        abstract="We report the development of GPT-4...",
    )
    assert s1.content_hash() == s2.content_hash()

    # Changing title or abstract alters content hash
    s3 = LiteratureSource(
        research_run_id="run_a",
        provider="arxiv",
        external_id="2303.08774",
        title="GPT-4 Technical Report (Revised)",
        authors=("OpenAI",),
        year=2023,
        abstract="We report the development of GPT-4...",
    )
    assert s1.content_hash() != s3.content_hash()


def test_literature_search_result_properties() -> None:
    """Verify LiteratureSearchResult structures and count property."""
    s = LiteratureSource(
        research_run_id="run_1",
        provider="test",
        external_id="ext_1",
        title="Paper Title",
    )
    res = LiteratureSearchResult(
        query="test query",
        provider="test",
        sources=(s,),
        total_results=42,
    )
    assert res.count == 1
    assert res.total_results == 42
    assert res.query == "test query"


def test_provider_capabilities() -> None:
    """Verify ProviderCapabilities defaults and immutability."""
    caps = ProviderCapabilities(provider_name="custom")
    assert caps.provider_name == "custom"
    assert caps.supports_abstract_search is True
    assert caps.max_batch_size == 50
