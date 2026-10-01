"""Unit tests for REX OpenAlex Provider (REX-029)."""

import httpx
import pytest
from pydantic import SecretStr

from rex.config.settings import LiteratureSettings
from rex.literature.base import (
    LiteratureTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from rex.literature.models import LiteratureSearchRequest
from rex.literature.openalex import (
    OpenAlexProvider,
    extract_openalex_id,
    reconstruct_openalex_abstract,
)


def test_reconstruct_openalex_abstract() -> None:
    """Verify deterministic abstract reconstruction from OpenAlex inverted index."""
    # Empty / None cases
    assert reconstruct_openalex_abstract(None) == ""
    assert reconstruct_openalex_abstract({}) == ""

    # Inverted index with words out of order
    inverted = {
        "is": [1],
        "Attention": [0],
        "you": [3],
        "all": [2],
        "need.": [4],
    }
    reconstructed = reconstruct_openalex_abstract(inverted)
    assert reconstructed == "Attention is all you need."

    # Words appearing at multiple positions
    multi_pos = {
        "the": [0, 4],
        "model": [1, 5],
        "learns": [2],
        "from": [3],
        "data.": [6],
    }
    assert reconstruct_openalex_abstract(multi_pos) == "the model learns from the model data."


def test_extract_openalex_id() -> None:
    """Verify OpenAlex URI normalization to clean ID."""
    assert extract_openalex_id("https://openalex.org/W2741809807") == "W2741809807"
    assert extract_openalex_id("W2741809807") == "W2741809807"
    assert extract_openalex_id("   https://openalex.org/W999   ") == "W999"


def test_openalex_search_normalization() -> None:
    """Verify OpenAlex search results normalize into typed LiteratureSource objects."""
    mock_payload = {
        "meta": {"count": 120, "page": 1, "per_page": 10},
        "results": [
            {
                "id": "https://openalex.org/W2741809807",
                "doi": "https://doi.org/10.48550/arXiv.1706.03762",
                "title": "Attention Is All You Need",
                "publication_year": 2017,
                "cited_by_count": 85000,
                "primary_location": {"landing_page_url": "https://arxiv.org/abs/1706.03762"},
                "authorships": [
                    {
                        "author": {
                            "id": "https://openalex.org/A1",
                            "display_name": "Ashish Vaswani",
                        }
                    },
                    {
                        "author": {
                            "id": "https://openalex.org/A2",
                            "display_name": "Noam Shazeer",
                        }
                    },
                ],
                "abstract_inverted_index": {
                    "Transformer": [0],
                    "architecture": [1],
                    "introduced.": [2],
                },
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "search=transformer" in str(request.url)
        assert "per-page=10" in str(request.url)
        return httpx.Response(200, json=mock_payload)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    settings = LiteratureSettings(
        openalex_email="rex@example.org",
        openalex_api_key=SecretStr("mock_secret_key_123"),
    )
    provider = OpenAlexProvider(settings=settings, client=client)

    req = LiteratureSearchRequest(query="transformer", limit=10)
    result = provider.search(req, research_run_id="run_exp_1")

    assert result.query == "transformer"
    assert result.provider == "openalex"
    assert result.total_results == 120
    assert len(result.sources) == 1

    source = result.sources[0]
    assert source.external_id == "W2741809807"
    assert source.title == "Attention Is All You Need"
    assert source.authors == ("Ashish Vaswani", "Noam Shazeer")
    assert source.year == 2017
    assert source.citation_count == 85000
    assert source.url == "https://arxiv.org/abs/1706.03762"
    assert source.abstract == "Transformer architecture introduced."


def test_openalex_polite_pool_and_api_key_headers() -> None:
    """Verify polite pool User-Agent mailto and Authorization header injection."""
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = LiteratureSettings(
        openalex_email="researcher@lab.org",
        openalex_api_key=SecretStr("super_secret_token"),
    )
    provider = OpenAlexProvider(settings=settings, client=client)

    provider.search(LiteratureSearchRequest(query="quantum"), research_run_id="run_1")

    assert "researcher@lab.org" in captured_headers["user-agent"]
    assert captured_headers["authorization"] == "Bearer super_secret_token"


def test_openalex_rate_limit_error() -> None:
    """Verify HTTP 429 is translated into RateLimitError with retry-after."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "15"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)

    with pytest.raises(RateLimitError) as exc_info:
        provider.search(LiteratureSearchRequest(query="test"), research_run_id="run_1")

    assert exc_info.value.retry_after == 15.0
    assert exc_info.value.provider == "openalex"


def test_openalex_server_and_timeout_errors() -> None:
    """Verify 5xx and timeout exceptions are typed correctly."""

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client_503 = httpx.Client(transport=httpx.MockTransport(error_handler))
    provider_503 = OpenAlexProvider(client=client_503)

    with pytest.raises(ProviderUnavailableError):
        provider_503.search(LiteratureSearchRequest(query="test"), research_run_id="run_1")

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Timeout connecting")

    client_timeout = httpx.Client(transport=httpx.MockTransport(timeout_handler))
    provider_timeout = OpenAlexProvider(client=client_timeout)

    with pytest.raises(LiteratureTimeoutError):
        provider_timeout.search(LiteratureSearchRequest(query="test"), research_run_id="run_1")


def test_openalex_get_by_id() -> None:
    """Verify get_by_id retrieves individual work and returns None for 404."""
    mock_work = {
        "id": "https://openalex.org/W9999",
        "title": "Specific Paper Title",
        "publication_year": 2021,
        "abstract": "Single paper abstract.",
        "authorships": [{"author": {"id": "A1", "display_name": "Ada Lovelace"}}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "W9999" in str(request.url):
            return httpx.Response(200, json=mock_work)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(client=client)

    source = provider.get_by_id("W9999", research_run_id="run_1")
    assert source is not None
    assert source.external_id == "W9999"
    assert source.title == "Specific Paper Title"
    assert source.abstract == "Single paper abstract."

    missing = provider.get_by_id("W0000", research_run_id="run_1")
    assert missing is None
