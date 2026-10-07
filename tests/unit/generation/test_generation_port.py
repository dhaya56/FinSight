"""The generation boundary: the schema reaches the runtime, and failures separate.

Transport is faked at the ``httpx`` client, so none of this needs Ollama. What is pinned
is the contract the layers above depend on: a parsed object, a schema actually sent, and
three failure kinds that must not be collapsed into one — unavailable is degradable (§27.9),
shape is a configuration fault, and truncation means the model did not read the evidence.
"""

import json
from typing import Any

import httpx
import pytest

from finsight.generation.contract import parse_answer
from finsight.generation.fake import MODEL, FakeGenerator
from finsight.generation.ollama_generator import OllamaGenerator
from finsight.generation.port import (
    GenerationRequest,
    GenerationShapeError,
    GenerationTruncatedError,
    GenerationUnavailableError,
    Generator,
)

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"claims": {"type": "array"}},
    "required": ["claims"],
}

WINDOW = 8192


def envelope(
    completion: str = '{"claims": []}',
    *,
    prompt_tokens: int | None = 120,
    eval_tokens: int | None = 40,
) -> dict[str, Any]:
    body: dict[str, Any] = {"response": completion}
    if prompt_tokens is not None:
        body["prompt_eval_count"] = prompt_tokens
    if eval_tokens is not None:
        body["eval_count"] = eval_tokens
    return body


def generator(
    handler: object, *, window: int = WINDOW, temperature: float = 0.0
) -> OllamaGenerator:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OllamaGenerator(
        base_url="http://127.0.0.1:11434",
        model="llama3.1:8b",
        context_window=window,
        temperature=temperature,
        timeout_seconds=30.0,
        client=httpx.Client(transport=transport),
    )


def request(prompt: str = "a prompt") -> GenerationRequest:
    return GenerationRequest(prompt=prompt, schema=SCHEMA)


class TestTheRequest:
    def test_the_schema_is_sent_to_the_runtime(self) -> None:
        """The whole design: constrained decoding rather than a hopeful instruction."""
        seen: list[dict[str, Any]] = []

        def handler(received: httpx.Request) -> httpx.Response:
            seen.append(json.loads(received.content))
            return httpx.Response(200, json=envelope())

        generator(handler).generate(request())

        assert seen[0]["format"] == SCHEMA

    def test_temperature_and_window_are_sent(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(received: httpx.Request) -> httpx.Response:
            seen.append(json.loads(received.content))
            return httpx.Response(200, json=envelope())

        generator(handler, window=4096).generate(request())

        assert seen[0]["options"]["temperature"] == 0.0
        assert seen[0]["options"]["num_ctx"] == 4096

    def test_streaming_is_off(self) -> None:
        """A streamed response has no single JSON body to constrain or parse."""
        seen: list[dict[str, Any]] = []

        def handler(received: httpx.Request) -> httpx.Response:
            seen.append(json.loads(received.content))
            return httpx.Response(200, json=envelope())

        generator(handler).generate(request())

        assert seen[0]["stream"] is False

    def test_no_tools_are_offered(self) -> None:
        """§10.4: the model has no capabilities. Pinned so an adapter cannot add any."""
        seen: list[dict[str, Any]] = []

        def handler(received: httpx.Request) -> httpx.Response:
            seen.append(json.loads(received.content))
            return httpx.Response(200, json=envelope())

        generator(handler).generate(request())

        assert "tools" not in seen[0]
        assert "functions" not in seen[0]


class TestTheResult:
    def test_the_completion_is_parsed(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope('{"claims": [{"text": "x"}]}'))

        result = generator(handler).generate(request())

        assert result.payload == {"claims": [{"text": "x"}]}

    def test_token_counts_and_window_are_reported(self) -> None:
        """Carried so a caller can check truncation against the right window."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope(prompt_tokens=120, eval_tokens=40))

        result = generator(handler).generate(request())

        assert result.prompt_tokens == 120
        assert result.completion_tokens == 40
        assert result.context_window == WINDOW
        assert result.model == "llama3.1:8b"

    def test_elapsed_time_is_measured(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope())

        assert generator(handler).generate(request()).elapsed_ms >= 0

    def test_missing_token_counts_are_unknown_not_zero_sized(self) -> None:
        """Ollama omits these sometimes; a truncation check must not fire on absence."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope(prompt_tokens=None, eval_tokens=None))

        result = generator(handler).generate(request())

        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0


class TestUnavailable:
    def test_a_transport_failure_is_unavailable(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(GenerationUnavailableError, match="could not reach ollama"):
            generator(handler).generate(request())

    def test_a_server_error_is_unavailable(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(GenerationUnavailableError, match="500"):
            generator(handler).generate(request())

    def test_a_non_json_envelope_is_unavailable(self) -> None:
        """A proxy's error page, not the model's answer. Retrying may well work."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(GenerationUnavailableError, match="not JSON"):
            generator(handler).generate(request())


class TestShape:
    def test_a_completion_that_is_not_json_is_a_shape_error(self) -> None:
        """The runtime ignored the schema. Retrying reproduces it, so it is not degradable."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope("Here is the answer: ..."))

        with pytest.raises(GenerationShapeError, match="not valid JSON"):
            generator(handler).generate(request())

    def test_an_empty_completion_is_a_shape_error(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope("   "))

        with pytest.raises(GenerationShapeError, match="no completion text"):
            generator(handler).generate(request())

    def test_a_json_array_is_a_shape_error(self) -> None:
        """Valid JSON is not enough; the contract is an object."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope('["a", "b"]'))

        with pytest.raises(GenerationShapeError, match="not an object"):
            generator(handler).generate(request())


class TestTruncation:
    """The silent failure: Ollama cuts an over-long prompt and reports success.

    An answer built on a truncated prompt cites passages the model never read, so it reads
    as fully supported and is not. These are the tests that stop that reaching a reader.
    """

    def test_a_prompt_filling_the_window_is_refused(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope(prompt_tokens=WINDOW))

        with pytest.raises(GenerationTruncatedError, match="did not see all of the evidence"):
            generator(handler).generate(request())

    def test_a_prompt_within_the_margin_is_refused(self) -> None:
        """Ollama counts template tokens the caller never sent, so exactness is unsafe."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope(prompt_tokens=WINDOW - 2))

        with pytest.raises(GenerationTruncatedError):
            generator(handler).generate(request())

    def test_a_prompt_comfortably_inside_the_window_is_accepted(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope(prompt_tokens=WINDOW - 500))

        assert generator(handler).generate(request()).prompt_tokens == WINDOW - 500

    def test_truncation_is_refused_before_the_payload_is_decoded(self) -> None:
        """So a truncated answer is never available to be used by mistake."""

        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=envelope('{"claims": [{"text": "looks fine"}]}', prompt_tokens=WINDOW)
            )

        with pytest.raises(GenerationTruncatedError):
            generator(handler).generate(request())


class TestTheFake:
    def test_the_fake_satisfies_the_port(self) -> None:
        assert isinstance(FakeGenerator(), Generator)

    def test_the_real_adapter_satisfies_the_port(self) -> None:
        def handler(_received: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=envelope())

        assert isinstance(generator(handler), Generator)

    def test_it_returns_claims_and_records_the_call(self) -> None:
        fake = FakeGenerator(claims=("one.", "two."))

        result = fake.generate(request("the prompt"))

        assert len(result.payload["claims"]) == 2  # type: ignore[arg-type]
        assert fake.calls[0].prompt == "the prompt"
        assert result.model == MODEL

    def test_its_payload_satisfies_the_contract(self) -> None:
        """The guard the fake was missing.

        It omitted ``answerable`` for three commits, which nothing noticed until the answer
        path ran end to end and ``parse_answer`` rejected every canned response as a shape
        error. A fake whose payload the parser refuses exercises the error path and nothing
        else, so the contract is asserted here rather than discovered downstream.
        """
        parsed = parse_answer(FakeGenerator(claims=("one.",)).generate(request()).payload)

        assert parsed.answerable is True
        assert [claim.text for claim in parsed.claims] == ["one."]

    def test_it_can_report_the_passages_do_not_answer(self) -> None:
        parsed = parse_answer(
            FakeGenerator(answerable=False, claims=()).generate(request()).payload
        )

        assert parsed.answerable is False
        assert parsed.claims == []

    def test_it_can_be_asked_to_be_unavailable(self) -> None:
        with pytest.raises(GenerationUnavailableError):
            FakeGenerator(fails=True).generate(request())

    def test_it_can_be_asked_to_report_truncation(self) -> None:
        with pytest.raises(GenerationTruncatedError):
            FakeGenerator(truncated=True).generate(request())

    def test_it_can_be_asked_to_return_the_wrong_shape(self) -> None:
        assert "claims" not in FakeGenerator(malformed=True).generate(request()).payload

    def test_it_can_invent_a_numeral_for_the_gate_to_catch(self) -> None:
        """§27.3's whole purpose. A fake that never misbehaves cannot test the Gate."""
        result = FakeGenerator(invent_numeral="412.00 crore").generate(request())

        claims = result.payload["claims"]
        assert "412.00 crore" in str(claims)

    def test_it_can_cite_outside_the_evidence_set(self) -> None:
        result = FakeGenerator(citations=(1,), cite_outside=99).generate(request())

        assert 99 in result.payload["claims"][0]["citations"]  # type: ignore[index]
