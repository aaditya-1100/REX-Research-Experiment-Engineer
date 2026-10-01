"""REX Literature Domain Models and Schemas (REX-028).

Defines typed, immutable schemas for scholarly literature search requests,
provider responses, publication metadata, author representations, and provenance.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rex.domain.models import LiteratureSource
from rex.observability.events import freeze_value


class Author(BaseModel):
    """Immutable scholarly author representation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Author full display name")
    affiliations: tuple[str, ...] = Field(
        default_factory=tuple, description="Institutional affiliations"
    )
    external_ids: Mapping[str, str] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="External identifiers (e.g. ORCID, OpenAlex author ID)",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Author name cannot be empty.")
        return cleaned

    @field_validator("affiliations", mode="before")
    @classmethod
    def _validate_affiliations(cls, v: Any) -> tuple[str, ...]:
        if v is None:
            return ()
        if isinstance(v, (list, tuple, set)):
            return tuple(str(a).strip() for a in v if str(a).strip())
        return (str(v).strip(),)

    @field_validator("external_ids", mode="after")
    @classmethod
    def _freeze_ids(cls, v: Any) -> Mapping[str, str]:
        if v is None:
            return MappingProxyType({})
        return freeze_value(v)


class LiteratureSearchRequest(BaseModel):
    """Immutable query specification for scholarly search providers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(description="Scholarly search query or keywords")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum number of sources to return")
    offset: int = Field(default=0, ge=0, description="Offset for pagination")
    year_start: int | None = Field(
        default=None, ge=1800, le=2100, description="Earliest publication year"
    )
    year_end: int | None = Field(
        default=None, ge=1800, le=2100, description="Latest publication year"
    )
    cursor: str | None = Field(
        default=None, description="Optional provider cursor for deep pagination"
    )

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Literature search query cannot be empty.")
        return cleaned

    @field_validator("year_end")
    @classmethod
    def _validate_year_range(cls, v: int | None, info: Any) -> int | None:
        if v is not None:
            y_start = info.data.get("year_start")
            if y_start is not None and v < y_start:
                raise ValueError("year_end must be greater than or equal to year_start.")
        return v


class LiteratureSearchResult(BaseModel):
    """Immutable structured result set returned by a scholarly search provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(description="Executing query string")
    provider: str = Field(description="Scholarly provider name")
    sources: tuple[LiteratureSource, ...] = Field(
        default_factory=tuple, description="Normalized literature source entities"
    )
    total_results: int | None = Field(
        default=None, ge=0, description="Total matching publications reported by provider"
    )
    next_cursor: str | None = Field(default=None, description="Opaque cursor token for next page")
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="UTC timestamp of retrieval"
    )

    @property
    def count(self) -> int:
        """Number of sources in this specific page/result set."""
        return len(self.sources)


class ProviderCapabilities(BaseModel):
    """Capabilities and operational metadata for a scholarly provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_name: str = Field(description="Normalized provider identifier")
    supports_abstract_search: bool = Field(default=True)
    supports_citation_count: bool = Field(default=True)
    supports_doi_lookup: bool = Field(default=True)
    supports_year_filtering: bool = Field(default=True)
    supports_pagination: bool = Field(default=True)
    max_batch_size: int = Field(default=50)


__all__ = [
    "Author",
    "LiteratureSearchRequest",
    "LiteratureSearchResult",
    "LiteratureSource",
    "ProviderCapabilities",
]
