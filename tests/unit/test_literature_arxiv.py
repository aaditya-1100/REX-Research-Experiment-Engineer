"""Unit tests for REX arXiv Provider (REX-031)."""

import httpx
import pytest

from rex.literature.arxiv import ArXivProvider, extract_canonical_arxiv_id
from rex.literature.base import (
    MalformedResponseError,
)
from rex.literature.models import LiteratureSearchRequest

SAMPLE_ARXIV_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <link href="http://arxiv.org/api/query?search_query=all:transformer" rel="self" type="application/atom+xml"/>
  <title type="html">ArXiv Query: search_query=all:transformer</title>
  <opensearch:totalResults>1842</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T01:09:47Z</updated>
    <published>2017-06-12T17:58:24Z</published>
    <title>
      Attention Is All You Need
    </title>
    <summary>
      The dominant sequence transduction models are based on complex
      recurrent or convolutional neural networks.
    </summary>
    <author>
      <name>Ashish Vaswani</name>
    </author>
    <author>
      <name>Noam Shazeer</name>
    </author>
    <arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>
    <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
    <arxiv:primary_category term="cs.CL"/>
    <arxiv:comment>15 pages, 5 figures</arxiv:comment>
  </entry>
</feed>
"""


def test_extract_canonical_arxiv_id() -> None:
    """Verify arXiv ID cleaning and canonicalization."""
    assert extract_canonical_arxiv_id("http://arxiv.org/abs/2303.08774v1") == "2303.08774v1"
    assert (
        extract_canonical_arxiv_id("https://arxiv.org/abs/quant-ph/0101001") == "quant-ph/0101001"
    )
    assert extract_canonical_arxiv_id("arXiv:1706.03762") == "1706.03762"
    assert extract_canonical_arxiv_id("2303.08774") == "2303.08774"


def test_arxiv_search_and_xml_normalization() -> None:
    """Verify arXiv XML feed parsing into typed LiteratureSource objects."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "search_query=all%3Atransformer" in str(
            request.url
        ) or "search_query=all:transformer" in str(request.url)
        return httpx.Response(200, text=SAMPLE_ARXIV_ATOM_XML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ArXivProvider(client=client)

    req = LiteratureSearchRequest(query="transformer", limit=1)
    result = provider.search(req, research_run_id="run_arxiv_1")

    assert result.query == "transformer"
    assert result.provider == "arxiv"
    assert result.total_results == 1842
    assert len(result.sources) == 1

    source = result.sources[0]
    assert source.external_id == "arxiv:1706.03762v7"
    assert source.title == "Attention Is All You Need"
    assert source.authors == ("Ashish Vaswani", "Noam Shazeer")
    assert source.year == 2017
    assert source.doi == "10.48550/arXiv.1706.03762"
    assert "dominant sequence transduction models" in source.abstract
    assert source.url == "http://arxiv.org/abs/1706.03762v7"
    assert source.raw_metadata["primary_category"] == "cs.CL"
    assert source.raw_metadata["comment"] == "15 pages, 5 figures"


def test_arxiv_malformed_xml_handling() -> None:
    """Verify malformed XML triggers MalformedResponseError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<unclosed_tag>This is not valid XML")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ArXivProvider(client=client)

    with pytest.raises(MalformedResponseError):
        provider.search(LiteratureSearchRequest(query="test"), research_run_id="run_1")


def test_arxiv_get_by_id() -> None:
    """Verify get_by_id retrieves specific preprint by ID."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "id_list=1706.03762" in str(request.url):
            return httpx.Response(200, text=SAMPLE_ARXIV_ATOM_XML)
        return httpx.Response(
            200,
            text='<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ArXivProvider(client=client)

    source = provider.get_by_id("1706.03762", research_run_id="run_1")
    assert source is not None
    assert source.external_id == "arxiv:1706.03762v7"

    missing = provider.get_by_id("9999.99999", research_run_id="run_1")
    assert missing is None
