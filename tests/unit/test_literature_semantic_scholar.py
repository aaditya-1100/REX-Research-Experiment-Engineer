"""Unit tests for REX Semantic Scholar Provider (REX-030)."""

import httpx
import pytest
from pydantic import SecretStr

from rex.config.settings import LiteratureSettings
from rex.literature.base import (
    RateLimitError,
)
from rex.literature.models import LiteratureSearchRequest
from rex.literature.semantic_scholar import SemanticScholarProvider


def test_semantic_scholar_search_normalization() -> None:
    """Verify Semantic Scholar search payload normalization into LiteratureSource."""
    mock_payload = {
        "total": 350,
        "offset": 0,
        "next": 10,
        "data": [
            {
                "paperId": "a1b2c3d4e5",
                "externalIds": {"DOI": "10.1145/3318464.3389700", "CorpusId": 218765432},
                "title": "BERT: Pre-training of Deep Bidirectional Transformers",
                "abstract": "We introduce a new language representation model called BERT...",
                "year": 2019,
                "citationCount": 45000,
                "url": "https://www.semanticscholar.org/paper/a1b2c3d4e5",
                "authors": [
                    {"authorId": "auth_1", "name": "Jacob Devlin"},
                    {"authorId": "auth_2", "name": "Ming-Wei Chang"},
                ],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert "query=bert" in url_str
        assert "limit=10" in url_str
        return httpx.Response(200, json=mock_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = LiteratureSettings(
        semantic_scholar_api_key=SecretStr("mock_s2_key_xyz"),
    )
    provider = SemanticScholarProvider(settings=settings, client=client)

    req = LiteratureSearchRequest(query="bert", limit=10)
    result = provider.search(req, research_run_id="run_test_s2")

    assert result.query == "bert"
    assert result.provider == "semantic_scholar"
    assert result.total_results == 350
    assert result.next_cursor == "10"
    assert len(result.sources) == 1

    source = result.sources[0]
    assert source.external_id == "doi:10.1145/3318464.3389700"
    assert source.title == "BERT: Pre-training of Deep Bidirectional Transformers"
    assert source.authors == ("Jacob Devlin", "Ming-Wei Chang")
    assert source.year == 2019
    assert source.citation_count == 45000
    assert source.doi == "10.1145/3318464.3389700"
    assert "BERT" in source.abstract


def test_semantic_scholar_api_key_header() -> None:
    """Verify x-api-key header is sent safely."""
    captured_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"total": 0, "data": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = LiteratureSettings(semantic_scholar_api_key=SecretStr("secret_scholar_token_999"))
    provider = SemanticScholarProvider(settings=settings, client=client)

    provider.search(LiteratureSearchRequest(query="neural"), research_run_id="run_1")
    assert captured_headers["x-api-key"] == "secret_scholar_token_999"


def test_semantic_scholar_year_filtering() -> None:
    """Verify year range parameters in Semantic Scholar query."""
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json={"total": 0, "data": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SemanticScholarProvider(client=client)

    provider.search(
        LiteratureSearchRequest(query="llm", year_start=2021, year_end=2023),
        research_run_id="run_1",
    )
    assert "year=2021-2023" in captured_urls[0]


def test_semantic_scholar_rate_limiting() -> None:
    """Verify HTTP 429 is translated into RateLimitError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SemanticScholarProvider(client=client)

    with pytest.raises(RateLimitError) as exc_info:
        provider.search(LiteratureSearchRequest(query="test"), research_run_id="run_1")

    assert exc_info.value.retry_after == 30.0
    assert exc_info.value.provider == "semantic_scholar"


def test_semantic_scholar_get_by_id() -> None:
    """Verify get_by_id retrieves individual paper or returns None for 404."""
    mock_paper = {
        "paperId": "paper_xyz_123",
        "title": "Specific Paper Title",
        "year": 2020,
        "abstract": "Specific abstract text.",
        "authors": [{"name": "Claude Shannon"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "paper_xyz_123" in str(request.url):
            return httpx.Response(200, json=mock_paper)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SemanticScholarProvider(client=client)

    source = provider.get_by_id("paper_xyz_123", research_run_id="run_1")
    assert source is not None
    assert source.title == "Specific Paper Title"
    assert source.authors == ("Claude Shannon",)

    missing = provider.get_by_id("non_existent_paper", research_run_id="run_1")
    assert missing is None
