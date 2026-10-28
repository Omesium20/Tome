"""`OllamaMetadataBackend.preflight`, and the guard that used to pass vacuously.

No test here reaches a live Ollama server: `OllamaMetadataBackend` takes a
`client`, and every test injects a scripted one.

The case worth the file is `model_context_length`. It was read off the
`/api/list` entry's `details`, which carries `family`, `parameter_size` and
`quantization_level` and **never** `context_length` — so it was None for every
model, `within_model_limit` returned True unconditionally, and `num_ctx` went
ungated against the model's real ceiling. Nothing failed; the check simply
never ran. Half of the two-part `num_ctx` guard
(`docs/benchmarking-and-testing.md`) was decorative.

The value lives on `/api/show` under `model_info`, keyed by architecture
(`qwen2.context_length`), with the architecture at `general.architecture`.
"""

import pytest

from knowledge_pipeline.metadata_generator.ollama_backend import (
    MetadataGenerationError,
    OllamaMetadataBackend,
)

SYSTEM = "system prompt that is short enough to fit anything"


class FakeEntry:
    """One `/api/list` row. Carries the fields Ollama really sends."""

    def __init__(self, tag: str):
        self.model = tag
        self.details = FakeDetails()


class FakeDetails:
    """`/api/list` details, verbatim in shape — note no `context_length`."""

    def __init__(self):
        self.parent_model = ""
        self.format = "gguf"
        self.family = "qwen2"
        self.parameter_size = "7.6B"
        self.quantization_level = "Q4_K_M"


class FakeListing:
    def __init__(self, tags):
        self.models = [FakeEntry(t) for t in tags]


class FakeShowResponse:
    def __init__(self, modelinfo):
        self.modelinfo = modelinfo


class FakeClient:
    """Scripted `list()`/`show()`. `show` may be an Exception to raise."""

    def __init__(self, *, tags=("qwen2.5:7b",), modelinfo=None, show=None):
        self._tags = tuple(tags)
        self._modelinfo = (
            modelinfo
            if modelinfo is not None
            else {"general.architecture": "qwen2", "qwen2.context_length": 32768}
        )
        self._show = show
        self.show_calls: list[str] = []

    def list(self):
        return FakeListing(self._tags)

    def show(self, model):
        self.show_calls.append(model)
        if isinstance(self._show, Exception):
            raise self._show
        if self._show is not None:
            return self._show
        return FakeShowResponse(self._modelinfo)


def backend(client, *, model="qwen2.5:7b", num_ctx=12288):
    return OllamaMetadataBackend(model=model, num_ctx=num_ctx, client=client)


class TestTrainedContextLength:
    def test_reads_the_architecture_keyed_value_from_show(self):
        """The regression. This was None for every model before the fix."""
        pre = backend(FakeClient()).preflight(system_prompt=SYSTEM)
        assert pre.model_context_length == 32768
        assert pre.within_model_limit is True

    def test_list_details_alone_cannot_supply_it(self):
        """Guards the original bug: `details` has no `context_length` at all.

        If someone reverts to reading the list entry, `show` stops being
        called and this fails — which is the point.
        """
        client = FakeClient()
        backend(client).preflight(system_prompt=SYSTEM)
        assert client.show_calls == ["qwen2.5:7b"]
        assert not hasattr(FakeDetails(), "context_length")

    def test_falls_back_to_a_suffix_scan(self):
        """A model whose `general.architecture` disagrees with its own prefix."""
        client = FakeClient(
            modelinfo={"general.architecture": "mystery", "llama.context_length": 8192}
        )
        pre = backend(client).preflight(system_prompt=SYSTEM)
        assert pre.model_context_length == 8192

    @pytest.mark.parametrize(
        "modelinfo",
        [
            {},
            {"general.architecture": "qwen2"},
            {"general.architecture": "qwen2", "qwen2.context_length": 0},
            {"general.architecture": "qwen2", "qwen2.context_length": "32768"},
            None,
        ],
        ids=["empty", "no-key", "zero", "not-an-int", "no-modelinfo"],
    )
    def test_unusable_values_degrade_to_none(self, modelinfo):
        show = FakeShowResponse(modelinfo) if modelinfo is not None else object()
        pre = backend(FakeClient(show=show)).preflight(system_prompt=SYSTEM)
        assert pre.model_context_length is None
        assert pre.within_model_limit is True

    def test_a_failing_show_never_breaks_preflight(self):
        """`fits_context` is preflight's real job; an advisory must not kill it."""
        client = FakeClient(show=RuntimeError("connection reset"))
        pre = backend(client).preflight(system_prompt=SYSTEM)
        assert pre.model_context_length is None
        assert pre.within_model_limit is True
        assert pre.fits_context is True
        assert pre.problems() == []

    def test_show_is_skipped_for_a_model_that_is_not_pulled(self):
        """No point asking the server about a tag it does not have."""
        client = FakeClient(tags=("qwen2.5:7b",))
        pre = backend(client, model="absent:14b").preflight(system_prompt=SYSTEM)
        assert client.show_calls == []
        assert pre.model_exists is False
        assert pre.model_context_length is None
        assert "is not pulled" in pre.problems()[0]


class TestWithinModelLimit:
    def test_num_ctx_above_the_trained_ceiling_is_now_caught(self):
        """The check this whole fix exists to make reachable."""
        pre = backend(FakeClient(), num_ctx=40000).preflight(system_prompt=SYSTEM)
        assert pre.within_model_limit is False
        assert any("exceeds" in p and "32,768" in p for p in pre.problems())

    def test_num_ctx_at_the_ceiling_exactly_is_allowed(self):
        pre = backend(FakeClient(), num_ctx=32768).preflight(system_prompt=SYSTEM)
        assert pre.within_model_limit is True

    def test_the_two_guards_are_independent(self):
        """A too-small num_ctx is still the louder problem, and reported first."""
        huge = "x" * 200_000
        pre = backend(FakeClient(), num_ctx=4096).preflight(system_prompt=huge)
        assert pre.fits_context is False
        assert pre.within_model_limit is True
        assert "too small" in pre.problems()[0]


class TestUnreachableServer:
    def test_a_failing_list_still_raises(self):
        """Unchanged behavior: no server means no run, loudly."""

        class Dead:
            def list(self):
                raise OSError("connection refused")

        with pytest.raises(MetadataGenerationError, match="Could not reach"):
            backend(Dead()).preflight(system_prompt=SYSTEM)
