"""The staged-artifact contract between our staging script and Docling.

This is a contract test against the installed library, not a test of our own
arithmetic, and it exists because the failure mode is **silent**. Docling looks
for a staged model at ``artifacts_path / <repo_id with "/" replaced by "-->``. If
our staging script writes to any other folder name, Docling does not error — it
concludes the model is absent and downloads it mid-parse, which §20.6 forbids and
which the restricted parser worker (§11.7) would fail closed on.

Nothing about that failure is visible in a passing extraction, so the folder
naming is pinned here against the library's own declared constants. A Docling
upgrade that renames them breaks this test instead of quietly reintroducing a
parse-time download.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "stage_docling_models.py"


def _staging_module() -> Any:
    """Load the staging script as a module.

    By path because ``scripts/`` is deliberately not an importable package: these
    are operator entry points, not library code, and making them importable would
    invite production code to depend on them.
    """
    spec = importlib.util.spec_from_file_location("stage_docling_models", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFolderNaming:
    def test_folder_replaces_the_repo_separator(self) -> None:
        staging = _staging_module()
        artifact = staging.Artifact(
            repo_id="org/model", revision="main", pin="0" * 40, purpose="test"
        )
        assert artifact.folder == "org--model"

    def test_tableformer_folder_matches_doclings_own_constant(self) -> None:
        """The name Docling looks for, taken from Docling rather than assumed."""
        model = pytest.importorskip(
            "docling.models.stages.table_structure.table_structure_model"
        )
        staging = _staging_module()
        staged = {a.repo_id: a.folder for a in staging.REQUIRED}
        expected = model.TableStructureModel._model_repo_folder

        assert staged["docling-project/docling-models"] == expected

    def test_layout_folder_matches_doclings_resolver(self) -> None:
        """The layout model resolves through a different code path to the same shape."""
        options = pytest.importorskip("docling.datamodel.pipeline_options")
        staging = _staging_module()
        spec = options.PdfPipelineOptions().layout_options.model_spec

        staged = {a.repo_id: a.folder for a in staging.REQUIRED}
        assert spec.repo_id in staged
        assert staged[spec.repo_id] == spec.repo_id.replace("/", "--")


class TestManifest:
    def test_every_artifact_is_pinned_to_a_commit(self) -> None:
        """A branch is not a revision.

        Docling asks for ``main`` for the layout model, so a model could move
        under a recorded measurement and invalidate it. ENV-007 open item 8.
        """
        staging = _staging_module()
        for artifact in staging.REQUIRED:
            assert len(artifact.pin) == 40, artifact.repo_id
            assert all(c in "0123456789abcdef" for c in artifact.pin), artifact.repo_id

    def test_the_manifest_covers_what_the_configured_pipeline_requests(self) -> None:
        """Guards against a Docling upgrade needing an artifact we do not stage.

        Only the layout model is checkable this way — TableFormer's repository is
        a class constant rather than a pipeline option — so this is a partial
        check, and the staging script's own verification step is what catches the
        rest by failing loudly.
        """
        options = pytest.importorskip("docling.datamodel.pipeline_options")
        staging = _staging_module()
        requested = options.PdfPipelineOptions().layout_options.model_spec.repo_id

        assert requested in {a.repo_id for a in staging.REQUIRED}

    def test_ocr_is_off_so_no_ocr_artifact_is_needed(self) -> None:
        """The manifest's size depends on this, and §12.11 defers OCR.

        Docling's default pipeline initialises an OCR model and fetches it from a
        third-party host. The adapter disables it; if that ever regresses, the
        staged set is incomplete and a parse would reach the network for a
        capability this project has not adopted.
        """
        options = pytest.importorskip("docling.datamodel.base_models")
        from finsight.extraction.pdf.docling_tables import DoclingTableDetector

        detector = DoclingTableDetector(artifacts_path="unused")
        built = detector._build()
        pipeline = built.format_to_options[options.InputFormat.PDF].pipeline_options

        assert pipeline.do_ocr is False

    def test_the_artifacts_path_reaches_the_pipeline(self) -> None:
        """Without this the whole staging exercise is decorative.

        ``artifacts_path`` is what turns a missing model into an exception rather
        than a download, so a constructor argument that failed to reach the
        pipeline would leave the §20.6 guarantee resting on the network being
        unreachable.
        """
        options = pytest.importorskip("docling.datamodel.base_models")
        from finsight.extraction.pdf.docling_tables import DoclingTableDetector

        detector = DoclingTableDetector(artifacts_path="model_cache/docling")
        built = detector._build()
        pipeline = built.format_to_options[options.InputFormat.PDF].pipeline_options

        assert str(pipeline.artifacts_path) == "model_cache/docling"
