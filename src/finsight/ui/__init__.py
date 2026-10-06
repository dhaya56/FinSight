"""The Streamlit frontend: an API-only client (§28.12, CLAUDE.md §6).

Nothing here reaches PostgreSQL, Qdrant, object storage or Ollama. The only way out of
this package is :mod:`finsight.ui.client`, which speaks HTTP to the authenticated API, and
the absence of any other import is what makes the "holds no data-store credentials" rule
structural rather than a convention. ``tests/unit/ui/test_ui_boundary.py`` enforces it.

**Most of this frontend is ahead of the backend.** Retrieval and the query trace answer
from the real corpus; the remaining pages render fixtures from :mod:`finsight.ui.demo` so
the interface for a planned capability can be reviewed before it exists. Every such panel
carries a badge from :mod:`finsight.ui.theme` saying so, and every issuer in the fixtures
is invented, so no figure shown can be mistaken for a claim about a real company.

Streamlit renders bare top-level string literals as page content, so modules in this
package document their constants with ``#`` comments rather than attribute docstrings.
"""
