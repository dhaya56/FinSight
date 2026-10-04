"""The enriched embedding string: a projection of columns, and nothing more."""

from finsight.indexing.enrichment import ChunkContext, embedded_text

BODY = "This was primarily due to increased cost of goods sold."


class TestEmbeddedText:
    def test_the_body_appears_verbatim_and_last(self) -> None:
        """§14.4: the enriched form is for search; the body must stay recognisable.

        Asserted on the suffix rather than on containment, so a future change that
        appended anything after the chunk text fails here.
        """
        composed = embedded_text(BODY, ChunkContext(issuer_name="Infosys Limited"))

        assert composed.endswith(BODY)

    def test_context_precedes_the_body_with_a_blank_line(self) -> None:
        composed = embedded_text(
            BODY, ChunkContext(issuer_name="Infosys Limited")
        )

        assert composed == f"Issuer: Infosys Limited\n\n{BODY}"

    def test_every_permitted_field_is_carried(self) -> None:
        composed = embedded_text(
            BODY,
            ChunkContext(
                issuer_name="HDFC Bank Limited",
                document_type="annual_report",
                fiscal_period="FY2024-25",
                reporting_basis="both",
                currency="INR",
                heading_path=("Directors' Report", "Capital Adequacy"),
                page_numbers=(42,),
            ),
        )

        assert composed.splitlines()[:6] == [
            "Issuer: HDFC Bank Limited",
            "Document: annual report",
            "Period: FY2024-25",
            "Basis: both",
            "Currency: INR",
            "Section: Directors' Report > Capital Adequacy",
        ]
        assert "Page: 42" in composed

    def test_an_absent_field_is_omitted_rather_than_marked_unknown(self) -> None:
        """"unknown" would be a token shared by every incomplete document.

        That makes documents similar to one another for a reason that has nothing
        to do with their content, which is the opposite of what enrichment is for.
        """
        composed = embedded_text(BODY, ChunkContext(fiscal_period="FY2024-25"))

        assert composed == f"Period: FY2024-25\n\n{BODY}"
        assert "unknown" not in composed.lower()

    def test_a_chunk_with_no_context_at_all_embeds_the_body_alone(self) -> None:
        """No leading blank line: it would shift the vector of every such chunk."""
        assert embedded_text(BODY, ChunkContext()) == BODY

    def test_a_document_type_loses_its_underscores(self) -> None:
        composed = embedded_text(BODY, ChunkContext(document_type="form_10k"))

        assert "Document: form 10k" in composed

    def test_a_single_page_is_labelled_in_the_singular(self) -> None:
        composed = embedded_text(BODY, ChunkContext(page_numbers=(7,)))

        assert "Page: 7" in composed
        assert "Pages" not in composed

    def test_a_span_is_rendered_as_its_endpoints(self) -> None:
        """Fewer tokens than the enumeration, and says the same thing."""
        composed = embedded_text(BODY, ChunkContext(page_numbers=(19, 20, 21)))

        assert "Pages: 19-21" in composed

    def test_an_empty_heading_path_produces_no_section_line(self) -> None:
        """Empty means "no heading found above this", which is common by design."""
        composed = embedded_text(BODY, ChunkContext(heading_path=()))

        assert "Section" not in composed

    def test_composition_is_deterministic(self) -> None:
        """Two chunks of identical context and text must embed identically."""
        context = ChunkContext(
            issuer_name="Infosys Limited",
            heading_path=("A", "B"),
            page_numbers=(3, 4),
        )

        assert embedded_text(BODY, context) == embedded_text(BODY, context)

    def test_units_as_presented_is_not_a_field(self) -> None:
        """Deliberate: one corpus entry records a hazard sentence there.

        Embedding "INR crore, lakh and million mixed within one document" into
        every chunk would assert a scale the document does not have.
        """
        assert not hasattr(ChunkContext(), "units_as_presented")
