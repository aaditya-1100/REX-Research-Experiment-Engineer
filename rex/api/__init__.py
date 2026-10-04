"""REX Web Application & HTTP API Service (REX-037).

Exposes RESTful endpoints and Server-Sent Events (SSE) for research run management,
experiment tracking, evidence graph lineage, formal verification, and report retrieval.
"""

from rex.api.app import create_app

__all__ = ["create_app"]
