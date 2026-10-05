"""The UI holds no data-store credentials, enforced rather than documented.

CLAUDE.md §6 and §28.12 both say the Streamlit frontend is API-only. That is a
property of what the package imports, so it is checked by reading the imports — and
checked **statically**, with :mod:`ast`, rather than by importing the modules and
inspecting ``sys.modules``. Three reasons: importing the page module would render a
Streamlit page, an import inside a function would escape a ``sys.modules`` check
entirely, and a transitive import would make the result depend on what some other
test imported first.

The failure this prevents is small and plausible: someone needs a default from
``Settings``, imports it for convenience, and the UI process can now read the
PostgreSQL password. Nothing visibly breaks, so nothing catches it.
"""

import ast
from pathlib import Path

import pytest

import finsight.ui

FORBIDDEN_PREFIXES = (
    "finsight.config",
    "finsight.persistence",
    "finsight.vector_index",
    "finsight.object_store",
    "finsight.embedding",
    "finsight.reranking",
    "finsight.retrieval",
    "finsight.indexing",
    "finsight.chunking",
    "finsight.extraction",
    "finsight.corpus",
    "sqlalchemy",
    "alembic",
    "qdrant_client",
    "boto3",
    "botocore",
    "psycopg",
    "torch",
    "transformers",
)
"""Everything the UI must not be able to reach.

Credential holders first: settings, persistence, the vector index and object storage
each carry or construct a credential. The retrieval and model packages follow for a
different reason — importing them would put the pipeline *in the UI process*, which
is the same violation by another route: a frontend that retrieves locally is not an
API-only frontend, whatever it holds.
"""

UI_DIRECTORY = Path(finsight.ui.__file__).parent


def ui_modules() -> list[Path]:
    return sorted(UI_DIRECTORY.rglob("*.py"))


def imported_names(source: str) -> set[str]:
    """Every module name the source imports, at any nesting depth.

    ``ast.walk`` rather than a scan of top-level nodes, so an import tucked inside a
    function or a ``try`` block is found too — which is exactly where a convenience
    import would be put.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


class TestImportBoundary:
    def test_the_package_has_modules_to_check(self) -> None:
        """A boundary test over an empty directory passes and proves nothing."""
        assert len(ui_modules()) >= 2

    @pytest.mark.parametrize("module", ui_modules(), ids=lambda path: path.name)
    def test_no_module_imports_a_data_store_or_a_model(self, module: Path) -> None:
        offending = {
            name
            for name in imported_names(module.read_text(encoding="utf-8"))
            for prefix in FORBIDDEN_PREFIXES
            if name == prefix or name.startswith(f"{prefix}.")
        }

        assert not offending, (
            f"{module.name} imports {sorted(offending)}. The UI reaches FinSight only "
            f"through the authenticated API (§28.12)."
        )

    def test_the_check_would_catch_a_violation(self) -> None:
        """Guards the guard: a test that cannot fail is not protection."""
        source = "def handler():\n    from finsight.config.settings import get_settings\n"

        assert "finsight.config.settings" in imported_names(source)
