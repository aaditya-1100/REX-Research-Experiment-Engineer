"""REX OpenAlex Scholarly Literature Provider (REX-029).

Implements the LiteratureProvider interface for OpenAlex REST API, featuring
deterministic inverted-index abstract reconstruction, conservative rate limiting,
polite pool headers, safe API key masking, and typed error handling.
"""

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from rex.config.settings import LiteratureSettings
from rex.literature.base import (
    MAX_RESPONSE_BYTES,
    MAX_RETRY_AFTER_SECONDS,
    InvalidQueryError,
    LiteratureProvider,
    LiteratureProviderError,
    LiteratureTimeoutError,
    MalformedResponseError,
    ProviderUnavailableError,
    RateLimitError,
    mask_sensitive_headers,
    sanitize_secret_values,
    validate_safe_url,
)
from rex.literature.models import (
    LiteratureSearchRequest,
    LiteratureSearchResult,
    LiteratureSource,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)


def reconstruct_openalex_abstract(
    abstract_inverted_index: Mapping[str, list[int]] | None,
) -> str:
    """Reconstruct contiguous plain-text abstract from OpenAlex's inverted index representation.

    In OpenAlex, abstracts are often stored as an inverted index mapping words to integer token
    positions. This helper deterministically reconstructs the original textual abstract.
    """
    if not abstract_inverted_index:
        return ""

    positions: dict[int, str] = {}
    for word, indices in abstract_inverted_index.items():
        if not isinstance(indices, list):
            continue
        for pos in indices:
            if isinstance(pos, int) and 0 <= pos <= 100000:
                positions[pos] = str(word)

    if not positions:
        return ""

    max_pos = max(positions.keys())
    tokens: list[str] = [positions.get(i, "") for i in range(max_pos + 1)]
    text = " ".join(t for t in tokens if t)
    return text.strip()


def extract_openalex_id(raw_id: str) -> str:
    """Normalize full OpenAlex URI (e.g. 'https://openalex.org/W2741809807') to canonical ID."""
    clean = raw_id.strip()
    if clean.startswith("https://openalex.org/"):
        clean = clean.replace("https://openalex.org/", "")
    elif clean.startswith("http://openalex.org/"):
        clean = clean.replace("http://openalex.org/", "")

    if "://" in clean or ".." in clean or clean.startswith("/") or "\\" in clean:
        raise InvalidQueryError(f"Malformed or unsafe OpenAlex external ID: '{clean}'")
    return clean


class OpenAlexProvider(LiteratureProvider):
    """Scholarly search and retrieval adapter for the OpenAlex API (REX-029)."""

    def __init__(
        self,
        settings: LiteratureSettings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or LiteratureSettings()
        self.base_url = self.settings.openalex_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=float(self.settings.request_timeout_seconds), follow_redirects=False
        )

    @property
    def provider_name(self) -> str:
        return "openalex"

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
        if self.settings.openalex_email:
            headers["User-Agent"] = f"REX-Research/0.1.0 (mailto:{self.settings.openalex_email})"
        if self.settings.openalex_api_key:
            headers["Authorization"] = f"Bearer {self.settings.openalex_api_key.get_secret_value()}"
        return headers

    def _execute_request(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute HTTP request with safe error translation and secret masking."""
        validate_safe_url(url, self.base_url)
        headers = self._get_headers()
        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise LiteratureTimeoutError(
                f"Request to OpenAlex timed out after {self.settings.request_timeout_seconds}s.",
                provider=self.provider_name,
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailableError(
                f"Network failure communicating with OpenAlex: {exc}",
                provider=self.provider_name,
            ) from exc
        except Exception as exc:
            raise LiteratureProviderError(
                f"Unexpected error communicating with OpenAlex: {exc}",
                provider=self.provider_name,
            ) from exc

        if response.status_code in (301, 302, 303, 307, 308):
            loc = response.headers.get("Location", "")
            raise LiteratureProviderError(
                f"HTTP redirect ({response.status_code}) to '{loc}' is not permitted.",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        if len(response.content) > MAX_RESPONSE_BYTES:
            raise MalformedResponseError(
                f"OpenAlex response exceeded size limit of {MAX_RESPONSE_BYTES} bytes.",
                provider=self.provider_name,
            )

        if response.status_code == 429:
            retry_after_hdr = response.headers.get("Retry-After")
            retry_after: float | None = None
            if retry_after_hdr:
                try:
                    retry_after = float(retry_after_hdr)
                except ValueError:
                    retry_after = None
            if retry_after is not None:
                if retry_after > MAX_RETRY_AFTER_SECONDS:
                    raise RateLimitError(
                        f"OpenAlex rate limit retry-after ({retry_after}s) exceeds allowable limit of {MAX_RETRY_AFTER_SECONDS}s.",
                        provider=self.provider_name,
                        retry_after=retry_after,
                    )
                retry_after = max(retry_after, 0.0)

            raise RateLimitError(
                "OpenAlex rate limit exceeded (HTTP 429).",
                provider=self.provider_name,
                retry_after=retry_after,
            )

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"OpenAlex server error (HTTP {response.status_code}).",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        if response.status_code == 404:
            return {}

        if response.status_code >= 400:
            err_snippet = sanitize_secret_values(
                response.text[:200], [self.settings.openalex_api_key]
            )
            raise LiteratureProviderError(
                f"OpenAlex client error (HTTP {response.status_code}): {err_snippet}",
                provider=self.provider_name,
                status_code=response.status_code,
                details={"headers": mask_sensitive_headers(dict(response.headers))},
            )

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise MalformedResponseError(
                    "OpenAlex response payload is not a valid JSON object.",
                    provider=self.provider_name,
                )
            return data
        except Exception as exc:
            raise MalformedResponseError(
                f"Failed to parse JSON response from OpenAlex: {exc}",
                provider=self.provider_name,
            ) from exc

    def _normalize_work(self, item: dict[str, Any], research_run_id: str) -> LiteratureSource:
        """Deterministically normalize an OpenAlex work item into a LiteratureSource."""
        raw_id = str(item.get("id") or "")
        external_id = extract_openalex_id(raw_id)
        if not external_id:
            external_id = f"openalex_{item.get('doi') or 'unknown'}"

        title = (item.get("title") or item.get("display_name") or "Untitled Publication").strip()

        # Authors
        authors: list[str] = []
        authorships = item.get("authorships")
        if isinstance(authorships, list):
            for auth in authorships:
                if isinstance(auth, dict):
                    author_meta = auth.get("author")
                    if isinstance(author_meta, dict):
                        display_name = author_meta.get("display_name")
                        if display_name and str(display_name).strip():
                            authors.append(str(display_name).strip())

        # Year
        year = item.get("publication_year")
        if not isinstance(year, int):
            year = None

        # Abstract (either pre-parsed or reconstructed from inverted index)
        abstract = ""
        if isinstance(item.get("abstract"), str) and item["abstract"].strip():
            abstract = item["abstract"].strip()
        elif "abstract_inverted_index" in item:
            abstract = reconstruct_openalex_abstract(item.get("abstract_inverted_index"))

        # Canonical landing URL & DOI
        doi = item.get("doi")
        if doi and not str(doi).strip():
            doi = None
        else:
            doi = str(doi).strip() if doi else None

        url = doi or raw_id or ""
        primary_loc = item.get("primary_location")
        if isinstance(primary_loc, dict):
            landing_url = primary_loc.get("landing_page_url")
            if landing_url:
                url = str(landing_url).strip()

        citation_count = item.get("cited_by_count")
        if not isinstance(citation_count, int):
            citation_count = None

        # Sanitize raw metadata: remove inverted index from persisted json to conserve database space
        clean_raw = dict(item)
        if "abstract_inverted_index" in clean_raw:
            clean_raw.pop("abstract_inverted_index")

        return LiteratureSource(
            research_run_id=research_run_id or "run_unspecified",
            provider=self.provider_name,
            external_id=external_id,
            title=title,
            authors=tuple(authors),
            year=year,
            abstract=abstract,
            url=url,
            citation_count=citation_count,
            doi=doi,
            raw_metadata=clean_raw,
        )

    def search(
        self, request: LiteratureSearchRequest, research_run_id: str = ""
    ) -> LiteratureSearchResult:
        """Search OpenAlex works matching the request parameters."""
        effective_limit = min(request.limit, self.settings.max_results_limit, 50)
        # OpenAlex uses 1-based page numbers
        page = (request.offset // effective_limit) + 1

        params: dict[str, Any] = {
            "search": request.query,
            "per-page": effective_limit,
            "page": page,
        }

        # Apply year filter if requested
        if request.year_start is not None or request.year_end is not None:
            filters: list[str] = []
            if request.year_start is not None and request.year_end is not None:
                if request.year_start == request.year_end:
                    filters.append(f"publication_year:{request.year_start}")
                else:
                    filters.append(f"publication_year:{request.year_start}-{request.year_end}")
            elif request.year_start is not None:
                filters.append(f"from_publication_date:{request.year_start}-01-01")
            elif request.year_end is not None:
                filters.append(f"to_publication_date:{request.year_end}-12-31")

            if filters:
                params["filter"] = ",".join(filters)

        url = f"{self.base_url}/works"
        data = self._execute_request(url, params=params)

        results_list = data.get("results", [])
        sources: list[LiteratureSource] = []
        if isinstance(results_list, list):
            for item in results_list:
                if isinstance(item, dict):
                    sources.append(self._normalize_work(item, research_run_id))

        meta = data.get("meta", {})
        total_results = meta.get("count") if isinstance(meta, dict) else len(sources)

        return LiteratureSearchResult(
            query=request.query,
            provider=self.provider_name,
            sources=tuple(sources),
            total_results=total_results if isinstance(total_results, int) else None,
            next_cursor=None,
        )

    def get_by_id(self, external_id: str, research_run_id: str = "") -> LiteratureSource | None:
        """Fetch a single work from OpenAlex by ID or DOI."""
        clean_id = extract_openalex_id(external_id)
        url = f"{self.base_url}/works/{clean_id}"
        data = self._execute_request(url)
        if not data:
            return None
        return self._normalize_work(data, research_run_id)

    def close(self) -> None:
        if self._owns_client and self._client:
            self._client.close()


__all__ = ["OpenAlexProvider", "extract_openalex_id", "reconstruct_openalex_abstract"]
