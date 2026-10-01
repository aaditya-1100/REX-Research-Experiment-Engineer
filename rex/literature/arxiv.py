"""REX arXiv Preprints Literature Provider (REX-031).

Implements the LiteratureProvider interface for the arXiv API, featuring safe
Atom/XML feed parsing, canonical arXiv ID normalization, and untrusted metadata handling.
"""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from rex.config.settings import LiteratureSettings
from rex.literature.base import (
    MAX_RESPONSE_BYTES,
    InvalidQueryError,
    LiteratureProvider,
    LiteratureProviderError,
    LiteratureTimeoutError,
    MalformedResponseError,
    ProviderUnavailableError,
    RateLimitError,
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

# Standard Atom & arXiv XML namespaces
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def extract_canonical_arxiv_id(raw_id: str) -> str:
    """Normalize raw arXiv URI or string (e.g. 'http://arxiv.org/abs/2303.08774v1') to clean ID."""
    clean = raw_id.strip()
    # Strip URL prefixes
    clean = re.sub(r"^https?://arxiv\.org/abs/", "", clean)
    clean = re.sub(r"^arxiv:", "", clean, flags=re.IGNORECASE)
    if "://" in clean or ".." in clean or clean.startswith("/") or "\\" in clean:
        raise InvalidQueryError(f"Malformed or unsafe arXiv external ID: '{clean}'")
    return clean


class ArXivProvider(LiteratureProvider):
    """Scholarly preprint search and metadata adapter for arXiv (REX-031)."""

    def __init__(
        self,
        settings: LiteratureSettings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or LiteratureSettings()
        self.base_url = self.settings.arxiv_base_url
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=float(self.settings.request_timeout_seconds), follow_redirects=False
        )

    @property
    def provider_name(self) -> str:
        return "arxiv"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name=self.provider_name,
            supports_abstract_search=True,
            supports_citation_count=False,  # arXiv does not provide citation counts natively
            supports_doi_lookup=True,
            supports_year_filtering=False,  # arXiv query syntax handles date ranges differently
            supports_pagination=True,
            max_batch_size=min(self.settings.max_results_limit, 50),
        )

    def _execute_query(self, params: dict[str, Any]) -> str:
        """Execute HTTP GET request against arXiv query endpoint and return raw XML text."""
        validate_safe_url(self.base_url, self.base_url)
        headers = {
            "Accept": "application/atom+xml, application/xml, text/xml",
            "User-Agent": "REX-Research/0.1.0",
        }
        try:
            response = self._client.get(self.base_url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise LiteratureTimeoutError(
                f"Request to arXiv timed out after {self.settings.request_timeout_seconds}s.",
                provider=self.provider_name,
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderUnavailableError(
                f"Network failure communicating with arXiv: {exc}",
                provider=self.provider_name,
            ) from exc
        except Exception as exc:
            raise LiteratureProviderError(
                f"Unexpected error communicating with arXiv: {exc}",
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
                f"arXiv response exceeded size limit of {MAX_RESPONSE_BYTES} bytes.",
                provider=self.provider_name,
            )

        if response.status_code == 429:
            raise RateLimitError(
                "arXiv rate limit encountered (HTTP 429).",
                provider=self.provider_name,
            )

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"arXiv server error (HTTP {response.status_code}).",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        if response.status_code >= 400:
            err_snippet = sanitize_secret_values(response.text[:200])
            raise LiteratureProviderError(
                f"arXiv client error (HTTP {response.status_code}): {err_snippet}",
                provider=self.provider_name,
                status_code=response.status_code,
            )

        # XML bomb / XXE prevention: forbid DOCTYPE and ENTITY declarations
        text_content = response.text
        lower_xml = text_content.lower()
        if "<!doctype" in lower_xml or "<!entity" in lower_xml:
            raise MalformedResponseError(
                "XML DOCTYPE and ENTITY declarations are forbidden in arXiv responses for security.",
                provider=self.provider_name,
            )

        return text_content

    def _parse_entry(self, entry: ET.Element, research_run_id: str) -> LiteratureSource:
        """Safely parse an Atom <entry> element into a LiteratureSource."""
        raw_id_elem = entry.find(f"{ATOM_NS}id")
        raw_id = raw_id_elem.text.strip() if raw_id_elem is not None and raw_id_elem.text else ""
        canonical_id = extract_canonical_arxiv_id(raw_id)
        external_id = f"arxiv:{canonical_id}" if canonical_id else "unknown_arxiv_entry"

        # Title
        title_elem = entry.find(f"{ATOM_NS}title")
        raw_title = title_elem.text if title_elem is not None and title_elem.text else "Untitled"
        title = " ".join(raw_title.split()).strip()

        # Summary / Abstract
        summary_elem = entry.find(f"{ATOM_NS}summary")
        raw_summary = summary_elem.text if summary_elem is not None and summary_elem.text else ""
        abstract = " ".join(raw_summary.split()).strip()

        # Authors
        authors: list[str] = []
        for author_elem in entry.findall(f"{ATOM_NS}author"):
            name_elem = author_elem.find(f"{ATOM_NS}name")
            if name_elem is not None and name_elem.text and name_elem.text.strip():
                authors.append(name_elem.text.strip())

        # Year from published or updated
        year: int | None = None
        published_elem = entry.find(f"{ATOM_NS}published")
        if published_elem is not None and published_elem.text:
            m = re.match(r"^(\d{4})", published_elem.text.strip())
            if m:
                year = int(m.group(1))

        # Links (URL / PDF)
        canonical_url = raw_id
        for link_elem in entry.findall(f"{ATOM_NS}link"):
            rel = link_elem.get("rel")
            href = link_elem.get("href")
            if rel == "alternate" and href:
                canonical_url = href
                break
            if href and not canonical_url:
                canonical_url = href

        # DOI if present
        doi_elem = entry.find(f"{ARXIV_NS}doi")
        doi = (
            doi_elem.text.strip()
            if doi_elem is not None and doi_elem.text and doi_elem.text.strip()
            else None
        )

        # Raw metadata
        primary_cat = entry.find(f"{ARXIV_NS}primary_category")
        primary_category_term = primary_cat.get("term") if primary_cat is not None else None
        comment_elem = entry.find(f"{ARXIV_NS}comment")
        comment_text = (
            comment_elem.text.strip() if comment_elem is not None and comment_elem.text else None
        )

        raw_meta = {
            "arxiv_id": canonical_id,
            "raw_id": raw_id,
            "published": published_elem.text.strip()
            if published_elem is not None and published_elem.text
            else None,
            "primary_category": primary_category_term,
            "comment": comment_text,
        }

        return LiteratureSource(
            research_run_id=research_run_id or "run_unspecified",
            provider=self.provider_name,
            external_id=external_id,
            title=title,
            authors=tuple(authors),
            year=year,
            abstract=abstract,
            url=canonical_url,
            citation_count=None,
            doi=doi,
            raw_metadata=raw_meta,
        )

    def search(
        self, request: LiteratureSearchRequest, research_run_id: str = ""
    ) -> LiteratureSearchResult:
        """Search preprints on arXiv matching the query."""
        effective_limit = min(request.limit, self.settings.max_results_limit, 50)

        # Format search query: if already formatted with prefixes (e.g. ti:, all:), preserve
        clean_q = request.query.strip()
        if not any(clean_q.startswith(p) for p in ("all:", "ti:", "au:", "abs:", "id:")):
            search_query = f"all:{clean_q}"
        else:
            search_query = clean_q

        params = {
            "search_query": search_query,
            "start": request.offset,
            "max_results": effective_limit,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        xml_text = self._execute_query(params)

        try:
            root = ET.fromstring(xml_text)
        except Exception as exc:
            raise MalformedResponseError(
                f"Failed to parse Atom XML from arXiv: {exc}",
                provider=self.provider_name,
            ) from exc

        sources: list[LiteratureSource] = []
        for entry in root.findall(f"{ATOM_NS}entry"):
            # Check if this entry is an error entry (arXiv returns an entry with id = error for some errors)
            id_elem = entry.find(f"{ATOM_NS}id")
            if id_elem is not None and id_elem.text and "error" in id_elem.text.lower():
                summary_elem = entry.find(f"{ATOM_NS}summary")
                err_msg = summary_elem.text if summary_elem is not None else "Unknown arXiv error"
                logger.warning("arXiv query returned error entry: %s", err_msg)
                continue

            sources.append(self._parse_entry(entry, research_run_id))

        # Total results reported in openSearch:totalResults
        total_results: int | None = None
        total_elem = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
        if total_elem is not None and total_elem.text:
            try:
                total_results = int(total_elem.text.strip())
            except ValueError:
                total_results = None

        return LiteratureSearchResult(
            query=request.query,
            provider=self.provider_name,
            sources=tuple(sources),
            total_results=total_results,
            next_cursor=str(request.offset + len(sources))
            if len(sources) == effective_limit
            else None,
        )

    def get_by_id(self, external_id: str, research_run_id: str = "") -> LiteratureSource | None:
        """Fetch a specific preprint from arXiv by canonical ID or URL."""
        canonical_id = extract_canonical_arxiv_id(external_id)
        if not canonical_id:
            return None

        params = {
            "id_list": canonical_id,
            "max_results": 1,
        }
        xml_text = self._execute_query(params)

        try:
            root = ET.fromstring(xml_text)
        except Exception as exc:
            raise MalformedResponseError(
                f"Failed to parse Atom XML for arXiv ID '{canonical_id}': {exc}",
                provider=self.provider_name,
            ) from exc

        entry = root.find(f"{ATOM_NS}entry")
        if entry is None:
            return None

        # Check if error entry
        id_elem = entry.find(f"{ATOM_NS}id")
        if id_elem is not None and id_elem.text and "error" in id_elem.text.lower():
            return None

        return self._parse_entry(entry, research_run_id)

    def close(self) -> None:
        if self._owns_client and self._client:
            self._client.close()


__all__ = ["ATOM_NS", "ArXivProvider", "extract_canonical_arxiv_id"]
