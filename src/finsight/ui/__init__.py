"""The Streamlit frontend: an API-only client (§28.12, CLAUDE.md §6).

Nothing here reaches PostgreSQL, Qdrant, object storage or Ollama. The only way out
of this package is :mod:`finsight.ui.client`, which speaks HTTP to the authenticated
API, and the absence of any other import is what makes the "holds no data-store
credentials" rule structural rather than a convention.
"""
