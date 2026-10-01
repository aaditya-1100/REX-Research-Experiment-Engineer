"""REX Semantic Scholar Literature Provider (REX-030).

Implements the LiteratureProvider interface for Semantic Scholar Academic Graph API,
normalizing paper metadata, citation graphs, external IDs, and respecting rate limits.
"""

import logging
from typing import Any

import httpx

from rex.config.settings import LiteratureSettings
from rex.literature.base import (
    LiteratureProvider,
    LiteratureProviderError,
    LiteratureTimeoutError,
    MalformedResponseError,
    ProviderUnavailableError,
    RateLimitError,
    mask_sensitive_headers,
)
from rex.literature.models import (
    LiteratureSearchRequest,
    LiteratureSearchResult,
    LiteratureSource,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)

# Standard fields requested from Semantic Scholar Academic Graph API
SEMANTIC_SCHOLAR_FIELDS = (
    "paperId,title,abstract,authors,year,citationCount,externalIds,url,publicationDate"
)


class SemanticScholarProvider(LiteratureProvider):
    """Scholarly search and retrieval adapter for the Semantic Scholar API (REX-030)."""

    def __init__(
        self,
        settings: LiteratureSettings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or LiteratureSettings()
        self.base_url = self.settings.semantic_scholar_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=float(self.settings.request_timeout_seconds))

    @property
    def provider_name(self) -> str:
        return "semantic_scholar"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.provider_name,
            supports_abstract_search=True,
            supports_citation_count=True,
            supports_doi_lookup=True,
            supports_year_filtering=True,
            supports_pagination=True,
            max_batch_size=min(self.settings.max_results_limit, 50),
        )

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "REX-Research/0.1.0",
        }
        if self.settings.semantic_scholar_api_key:
            headers["x-api-key"] = self.settings.semantic_scholar_api_key.get_secret_value()
        return headers

    def _execute_request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute HTTP request with strict error handling and secret sanitization."""
        headers = self._get_headers()
        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise LiteratureTimeoutError(
                f"Request to Semantic Scholar timed out after {self.settings.request_timeout_seconds}s.",
                provider=self.provider_name,
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailableError(
                f"Network failure communicating with Semantic Scholar: {exc}",
                provider=self.provider_name,
            ) from exc
        except Exception as exc:
            raise LiteratureProviderError(
                f"Unexpected error communicating with Semantic Scholar: {exc}",
                provider=self.provider_name,
            ) from exc

        if response.status_code == 429:
            retry_after_hdr = response.headers.get("Retry-After")
            retry_after: float | None = None
            if retry_after_hdr:
                try:
                    retry_after = float(retry_after_hdr)
                except ValueError:
                    retry_after = None
            raise RateLimitError(
                "Semantic Scholar rate limit exceeded (HTTP 429).",
                provider=self.provider_name,
                retry_after=retry_after,
            )

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"Semantic Scholar server error (HTTP {response.status_code}).",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        if response.status_code == 404:
            return {}

        if response.status_code >= 400:
            raise LiteratureProviderError(
                f"Semantic Scholar error (HTTP {response.status_code}): {response.text[:200]}",
                provider=self.provider_name,
                status_code=response.status_code,
                details={"headers": mask_sensitive_headers(dict(response.headers))},
            )

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise MalformedResponseError(
                    "Semantic Scholar response payload is not a valid JSON object.",
                    provider=self.provider_name,
                )
            return data
        except Exception as exc:
            raise MalformedResponseError(
                f"Failed to parse JSON response from Semantic Scholar: {exc}",
                provider=self.provider_name,
            ) from exc

    def _normalize_paper(self, item: dict[str, Any], research_run_id: str) -> LiteratureSource:
        """Deterministically normalize Semantic Scholar paper object into LiteratureSource."""
        paper_id = str(item.get("paperId") or "").strip()
        external_ids = item.get("externalIds") or {}

        # Prioritize DOI or CorpusId or paperId for external_id
        doi: str | None = None
        if isinstance(external_ids, dict):
            raw_doi = external_ids.get("DOI")
            if raw_doi:
                doi = str(raw_doi).strip()
            corpus_id = external_ids.get("CorpusId")
        else:
            corpus_id = None

        if doi:
            external_id = f"doi:{doi}"
        elif corpus_id:
            external_id = f"CorpusId:{corpus_id}"
        elif paper_id:
            external_id = f"s2:{paper_id}"
        else:
            external_id = "unknown_s2_source"

        title = (item.get("title") or "Untitled Publication").strip()

        # Authors
        authors: list[str] = []
        raw_authors = item.get("authors")
        if isinstance(raw_authors, list):
            for a in raw_authors:
                if isinstance(a, dict):
                    name = a.get("name")
                    if name and str(name).strip():
                        authors.append(str(name).strip())

        year = item.get("year")
        if not isinstance(year, int):
            year = None

        abstract = (item.get("abstract") or "").strip()

        # URL
        url = item.get("url") or (
            f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else ""
        )

        citation_count = item.get("citationCount")
        if not isinstance(citation_count, int):
            citation_count = None

        return LiteratureSource(
            research_run_id=research_run_id or "run_unspecified",
            provider=self.provider_name,
            external_id=external_id,
            title=title,
            authors=tuple(authors),
            year=year,
            abstract=abstract,
            url=str(url),
            citation_count=citation_count,
            doi=doi,
            raw_metadata=item,
        )

    def search(
        self, request: LiteratureSearchRequest, research_run_id: str = ""
    ) -> LiteratureSearchResult:
        """Search Semantic Scholar Academic Graph matching request."""
        effective_limit = min(request.limit, self.settings.max_results_limit, 50)
        params: dict[str, Any] = {
            "query": request.query,
            "offset": request.offset,
            "limit": effective_limit,
            "fields": SEMANTIC_SCHOLAR_FIELDS,
        }

        # Apply year filter if requested
        if request.year_start is not None or request.year_end is not None:
            if request.year_start is not None and request.year_end is not None:
                params["year"] = f"{request.year_start}-{request.year_end}"
            elif request.year_start is not None:
                params["year"] = f"{request.year_start}-"
            elif request.year_end is not None:
                params["year"] = f"-{request.year_end}"

        url = f"{self.base_url}/paper/search"
        data = self._execute_request(url, params=params)

        results_list = data.get("data", [])
        sources: list[LiteratureSource] = []
        if isinstance(results_list, list):
            for item in results_list:
                if isinstance(item, dict):
                    sources.append(self._normalize_paper(item, research_run_id))

        total_results = data.get("total")
        next_offset = data.get("next")
        next_cursor = str(next_offset) if next_offset is not None else None

        return LiteratureSearchResult(
            query=request.query,
            provider=self.provider_name,
            sources=tuple(sources),
            total_results=total_results if isinstance(total_results, int) else None,
            next_cursor=next_cursor,
        )

    def get_by_id(self, external_id: str, research_run_id: str = "") -> LiteratureSource | None:
        """Fetch a single paper by PaperId, DOI, CorpusId, or arXiv ID."""
        clean_id = external_id.strip()
        url = f"{self.base_url}/paper/{clean_id}"
        data = self._execute_request(url, params={"fields": SEMANTIC_SCHOLAR_FIELDS})
        if not data:
            return None
        return self._normalize_paper(data, research_run_id)

    def close(self) -> None:
        if self._owns_client and self._client:
            self._client.close()


__all__ = ["SEMANTIC_SCHOLAR_FIELDS", "SemanticScholarProvider"]
