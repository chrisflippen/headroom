"""Tests for headroom.transforms.tool_result_aging.

Covers the purity/idempotency contract (same input -> same output, aged
blocks never un-age as the conversation grows), the doubling-rung
boundary rule (rewrites of the cached prefix grow logarithmically with
the conversation, not linearly), the candidate-eligibility filters (keep_newest, already-marked,
non-text, CCR-tool results), and fail-open behavior when the tokenizer or
the CCR store errors.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from headroom.cache.compression_store import get_compression_store, reset_compression_store
from headroom.tokenizer import Tokenizer
from headroom.tokenizers.estimator import EstimatingTokenCounter
from headroom.transforms.tool_result_aging import (
    ToolResultAgingConfig,
    age_tool_results,
)


@pytest.fixture(autouse=True)
def _clean_store() -> Iterator[None]:
    reset_compression_store()
    yield
    reset_compression_store()


def _tool_result_message(tool_use_id: str, text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}],
    }


def _tool_use_message(
    tool_use_id: str, name: str, tool_input: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input or {}}
        ],
    }


def _big_text(n_chars: int, seed: str = "x") -> str:
    # Deterministic, non-repeating-enough-to-matter filler.
    return (seed * n_chars)[:n_chars]


def _make_conversation(
    n_pairs: int, chars_per_result: int = 4000, id_prefix: str = "tool"
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for i in range(n_pairs):
        tid = f"{id_prefix}_{i}"
        messages.append(_tool_use_message(tid, "Read", {"file_path": f"/f/{i}.py"}))
        messages.append(
            _tool_result_message(tid, f"result {i}: " + _big_text(chars_per_result, str(i % 10)))
        )
    return messages


def _config(**overrides: Any) -> ToolResultAgingConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "trigger_tokens": 1000,
        "keep_newest": 2,
        "first_batch_tokens": 2000,
        "ttl_seconds": 3600,
    }
    base.update(overrides)
    return ToolResultAgingConfig(**base)


@pytest.fixture
def tokenizer() -> Tokenizer:
    return Tokenizer(EstimatingTokenCounter())


def _aged_ids(messages: list[dict[str, Any]]) -> frozenset[str]:
    """tool_use_ids of every tool_result that is currently a stub."""
    ids: set[str] = set()
    for m in messages:
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        for block in m["content"]:
            text = block.get("content") if isinstance(block, dict) else None
            if isinstance(text, str) and "aged tool result" in text:
                ids.add(block["tool_use_id"])
    return frozenset(ids)


def _stubbed_texts(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for m in messages:
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        block = m["content"][0]
        text = block.get("content")
        if isinstance(text, str) and "aged tool result" in text:
            texts.append(text)
    return texts


class TestGating:
    def test_disabled_returns_input_unchanged(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30)
        result = age_tool_results(messages, tokenizer=tokenizer, config=_config(enabled=False))
        assert result.messages is messages
        assert result.aged_block_count == 0

    def test_below_trigger_returns_input_unchanged(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(2, chars_per_result=50)
        result = age_tool_results(
            messages, tokenizer=tokenizer, config=_config(trigger_tokens=1_000_000)
        )
        assert result.messages is messages
        assert result.aged_block_count == 0

    def test_no_tool_results_is_a_noop(self, tokenizer: Tokenizer) -> None:
        messages: list[dict[str, Any]] = [{"role": "user", "content": "hello " * 5000}]
        result = age_tool_results(messages, tokenizer=tokenizer, config=_config(trigger_tokens=10))
        assert result.messages is messages
        assert result.aged_block_count == 0


class TestBatching:
    def test_ages_only_whole_rungs_and_keeps_newest(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=3000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)

        assert result.aged_block_count > 0
        # The newest `keep_newest` tool_results must never be touched.
        tool_result_indices = [
            i
            for i, m in enumerate(result.messages)
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and m["content"][0].get("type") == "tool_result"
        ]
        for idx in tool_result_indices[-2:]:
            block = result.messages[idx]["content"][0]
            assert isinstance(block["content"], str)
            assert "aged tool result" not in block["content"]

    def test_nothing_is_aged_below_the_first_rung(self, tokenizer: Tokenizer) -> None:
        # 30 results of ~1k tokens each = ~28k candidate tokens: below a
        # 50k first rung nothing may be aged, however far past the trigger.
        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=50_000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)
        assert result.aged_block_count == 0
        assert result.messages is messages

    def test_aged_tokens_land_on_the_largest_rung_below_the_total(
        self, tokenizer: Tokenizer
    ) -> None:
        # Rungs are first, 2*first, 4*first, ... The aged set stops at the
        # first candidate whose running total reaches the largest rung that
        # fits, so aged_tokens is at least that rung and at most one block over.
        messages = _make_conversation(60, chars_per_result=4000)
        first = 3000
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=first)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)

        per_block = tokenizer.count_text(messages[1]["content"][0]["content"])
        candidate_total = per_block * (60 - 2)
        rung = first
        while rung * 2 <= candidate_total:
            rung *= 2
        assert rung <= result.aged_tokens < rung + per_block

    def test_prefix_rewrites_grow_logarithmically_not_linearly(self, tokenizer: Tokenizer) -> None:
        # Grow one conversation a block at a time and count how often the
        # aged set changes. Every change is a rewrite of the provider's
        # cached prefix, which costs a full re-send of the conversation, so
        # the count must be logarithmic in the conversation's size. With
        # fixed 2k batches this walk would rewrite ~30 times; with doubling
        # rungs it is bounded by log2(total / first) + 1.
        first = 2000
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=first)
        messages: list[dict[str, Any]] = []
        previous: frozenset[str] = frozenset()
        rewrites = 0
        for i in range(64):
            messages = messages + _make_conversation(1, chars_per_result=4000, id_prefix=f"g{i}")
            result = age_tool_results(messages, tokenizer=tokenizer, config=config)
            aged = _aged_ids(result.messages)
            if aged != previous:
                rewrites += 1
            previous = aged

        per_block = tokenizer.count_text(messages[1]["content"][0]["content"])
        candidate_total = per_block * (64 - 2)
        max_rewrites = 1
        rung = first
        while rung * 2 <= candidate_total:
            rung *= 2
            max_rewrites += 1
        assert rewrites <= max_rewrites
        assert rewrites >= 2  # it did age, and it did step up at least once

    def test_purity_same_input_same_output(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=3000)

        r1 = age_tool_results(messages, tokenizer=tokenizer, config=config)
        # Store keying is content-derived; a fresh store must reproduce the
        # identical hash for identical text, so the output is unchanged.
        reset_compression_store()
        r2 = age_tool_results(messages, tokenizer=tokenizer, config=config)

        assert list(r1.messages) == list(r2.messages)
        assert r1.aged_block_count == r2.aged_block_count

    def test_monotonic_growth_never_unages(self, tokenizer: Tokenizer) -> None:
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=3000)
        base = _make_conversation(20, chars_per_result=4000, id_prefix="base")
        r1 = age_tool_results(base, tokenizer=tokenizer, config=config)

        # Appended tail uses a distinct id prefix so its tool_use ids never
        # collide with the base conversation's.
        grown = base + _make_conversation(10, chars_per_result=4000, id_prefix="tail")
        r2 = age_tool_results(grown, tokenizer=tokenizer, config=config)

        # Every block aged in r1 must still be a stub (same or aged further) in r2.
        for i in range(len(r1.messages)):
            m1 = r1.messages[i]
            if m1.get("role") != "user" or not isinstance(m1.get("content"), list):
                continue
            block1 = m1["content"][0]
            if block1.get("type") != "tool_result":
                continue
            text1 = block1.get("content")
            if isinstance(text1, str) and "aged tool result" in text1:
                block2 = r2.messages[i]["content"][0]
                text2 = block2.get("content")
                assert isinstance(text2, str) and "aged tool result" in text2

    def test_first_batch_tokens_zero_disables_aging_safely(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=0)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)
        assert result.aged_block_count == 0


class TestStubFormat:
    def test_stub_contains_retrievable_hash_and_tool_name(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=3000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)

        assert result.aged_hashes
        stubbed_texts = _stubbed_texts(result.messages)
        assert stubbed_texts
        for text in stubbed_texts:
            lines = text.splitlines()
            assert len(lines) == 3
            assert lines[0].startswith("[aged tool result")
            assert "Read" in lines[0]
            assert lines[2].startswith("Retrieve original: hash=")

    def test_retrievable_from_ccr_store(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=3000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)

        assert result.aged_hashes
        store = get_compression_store()
        for _tool_use_id, hash_key in result.aged_hashes:
            entry = store.retrieve(hash_key)
            assert entry is not None


class TestEligibilityFilters:
    def test_already_marked_block_is_skipped(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        # Pre-mark an early tool_result as already aged/retrievable.
        messages[1]["content"][0]["content"] = "Retrieve original: hash=deadbeef"
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=1000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)
        # It should pass through byte-identical, not be re-wrapped.
        untouched = result.messages[1]["content"][0]["content"]
        assert untouched == "Retrieve original: hash=deadbeef"

    def test_non_text_tool_result_is_skipped(self, tokenizer: Tokenizer) -> None:
        messages = _make_conversation(30, chars_per_result=4000)
        messages[1]["content"][0]["content"] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}
        ]
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=1000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)
        block = result.messages[1]["content"][0]
        assert block["content"][0]["type"] == "image"

    def test_ccr_retrieve_tool_result_is_skipped(self, tokenizer: Tokenizer) -> None:
        from headroom.ccr.tool_injection import CCR_TOOL_NAME

        messages = _make_conversation(30, chars_per_result=4000)
        messages.insert(0, _tool_use_message("ccr_1", CCR_TOOL_NAME, {"hash": "abc"}))
        messages.insert(1, _tool_result_message("ccr_1", _big_text(4000, "z")))
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=1000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)
        block = result.messages[1]["content"][0]
        assert "aged tool result" not in block["content"]


class TestFailOpen:
    def test_tokenizer_count_messages_failure_fails_open(self) -> None:
        messages = _make_conversation(10)

        class BadTokenizer:
            def count_messages(self, _messages: list[dict[str, Any]]) -> int:
                raise RuntimeError("boom")

            def count_text(self, _text: str) -> int:
                raise RuntimeError("boom")

        result = age_tool_results(messages, tokenizer=BadTokenizer(), config=_config())
        assert result.messages is messages
        assert result.aged_block_count == 0

    def test_ccr_store_failure_leaves_block_untouched(
        self, tokenizer: Tokenizer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import headroom.cache.compression_store as store_mod

        class ExplodingStore:
            def store(self, *args: Any, **kwargs: Any) -> str:
                raise RuntimeError("store is down")

        monkeypatch.setattr(store_mod, "get_compression_store", lambda: ExplodingStore())

        messages = _make_conversation(30, chars_per_result=4000)
        config = _config(trigger_tokens=1000, keep_newest=2, first_batch_tokens=1000)
        result = age_tool_results(messages, tokenizer=tokenizer, config=config)
        assert result.aged_block_count == 0
        assert result.messages is messages


class TestConfigFromEnv:
    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "HEADROOM_AGING_ENABLED",
            "HEADROOM_AGING_TRIGGER_TOKENS",
            "HEADROOM_AGING_KEEP_NEWEST",
            "HEADROOM_AGING_FIRST_BATCH_TOKENS",
            "HEADROOM_AGING_TTL_SECONDS",
        ):
            monkeypatch.delenv(var, raising=False)
        config = ToolResultAgingConfig.from_env()
        assert config.enabled is True
        assert config.trigger_tokens == 150_000
        assert config.keep_newest == 20
        assert config.first_batch_tokens == 128_000
        assert config.ttl_seconds == 36 * 60 * 60

    def test_from_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HEADROOM_AGING_ENABLED", "false")
        monkeypatch.setenv("HEADROOM_AGING_TRIGGER_TOKENS", "5000")
        config = ToolResultAgingConfig.from_env()
        assert config.enabled is False
        assert config.trigger_tokens == 5000

    def test_from_env_invalid_int_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEADROOM_AGING_TRIGGER_TOKENS", "not-a-number")
        config = ToolResultAgingConfig.from_env()
        assert config.trigger_tokens == 150_000
