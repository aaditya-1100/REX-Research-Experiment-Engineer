"""REX Literature Intelligence Plane (Epic 7: REX-028 to REX-032).

Provides provider-neutral scholarly literature discovery, retrieval, deterministic normalization,
cryptographic content hashing, cross-run evidence graph integration, and adversarial
prompt-injection containment.
"""

from rex.literature.arxiv import ArXivProvider, extract_canonical_arxiv_id
from rex.literature.base import (
    InvalidQueryError,
    LiteratureError,
    LiteratureProvider,
    LiteratureProviderError,
    LiteratureSecurityError,
    LiteratureTimeoutError,
    MalformedResponseError,
    PromptInjectionAttemptError,
    ProviderUnavailableError,
    RateLimitError,
    mask_sensitive_headers,
)
from rex.literature.models import (
    Author,
    LiteratureSearchRequest,
    LiteratureSearchResult,
    LiteratureSource,
    ProviderCapabilities,
)
from rex.literature.openalex import (
    OpenAlexProvider,
    extract_openalex_id,
    reconstruct_openalex_abstract,
)
from rex.literature.semantic_scholar import SemanticScholarProvider
from rex.literature.service import LiteratureService
from rex.literature.trust import (
    InjectionDetector,
    InjectionRiskLevel,
    InjectionScanResult,
    LiteratureSanitizer,
    assert_literature_cannot_execute,
    build_literature_prompt_context,
)

__all__ = [
    "ArXivProvider",
    "Author",
    "InjectionDetector",
    "InjectionRiskLevel",
    "InjectionScanResult",
    "InvalidQueryError",
    "LiteratureError",
    "LiteratureProvider",
    "LiteratureProviderError",
    "LiteratureSanitizer",
    "LiteratureSearchRequest",
    "LiteratureSearchResult",
    "LiteratureSecurityError",
    "LiteratureService",
    "LiteratureSource",
    "LiteratureTimeoutError",
    "MalformedResponseError",
    "OpenAlexProvider",
    "PromptInjectionAttemptError",
    "ProviderCapabilities",
    "ProviderUnavailableError",
    "RateLimitError",
    "SemanticScholarProvider",
    "assert_literature_cannot_execute",
    "build_literature_prompt_context",
    "extract_canonical_arxiv_id",
    "extract_openalex_id",
    "mask_sensitive_headers",
    "reconstruct_openalex_abstract",
]
