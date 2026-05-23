from __future__ import annotations

from collections.abc import Callable

import pytest

from recordings.summarize.chunking import (
    DEFAULT_OUTPUT_TOKENS,
    DEFAULT_PROMPT_OVERHEAD,
    MAX_PARTIALS,
    Segment,
    chunk_token_budget,
    plan_chunks,
)


def _fake_counter(per_word_tokens: int = 1) -> Callable[[str], int]:
    return lambda text: len(text.split()) * per_word_tokens


def test_budget_math() -> None:
    assert chunk_token_budget(n_ctx=8192) == 8192 - DEFAULT_PROMPT_OVERHEAD - DEFAULT_OUTPUT_TOKENS


def test_budget_rejects_too_small_context() -> None:
    with pytest.raises(ValueError):
        chunk_token_budget(n_ctx=1024)


def test_single_pass_for_short_transcript() -> None:
    segs = [Segment(0, 1, "Hello world."), Segment(1, 2, "Goodbye.")]
    plan = plan_chunks(
        segs, "Hello world. Goodbye.",
        count_tokens=_fake_counter(),
        n_ctx=8192,
    )
    assert plan.fits_single_pass is True
    assert len(plan.chunks) == 1
    assert plan.chunks[0].text == "Hello world. Goodbye."


def test_map_reduce_split_contiguous() -> None:
    # 10 segments, each "huge" — 2000 tokens per segment, 20000 total.
    # Default n_ctx=8192 → budget ≈ 6500. Should split into ~3-4 chunks.
    segs = [Segment(i, i + 1, "word " * 100) for i in range(10)]
    transcript = " ".join(s.text for s in segs)
    plan = plan_chunks(
        segs, transcript,
        count_tokens=_fake_counter(per_word_tokens=20),
    )
    assert plan.fits_single_pass is False
    assert len(plan.chunks) >= 2
    # Every chunk respects the budget.
    for c in plan.chunks:
        assert c.token_estimate <= plan.chunk_token_budget
    # Chunks cover every segment exactly once and are contiguous.
    for prev, nxt in zip(plan.chunks[:-1], plan.chunks[1:], strict=True):
        assert nxt.segment_start_index == prev.segment_end_index + 1
    assert plan.chunks[0].segment_start_index == 0
    assert plan.chunks[-1].segment_end_index == len(segs) - 1


def test_raises_when_too_many_chunks() -> None:
    segs = [Segment(i, i + 1, "x " * 100) for i in range(MAX_PARTIALS * 4)]
    transcript = " ".join(s.text for s in segs)
    with pytest.raises(ValueError):
        plan_chunks(
            segs, transcript,
            count_tokens=_fake_counter(),
            n_ctx=512,
            prompt_overhead=400,
            output_tokens=80,
        )


def test_empty_segments_returns_single_chunk() -> None:
    plan = plan_chunks(
        [], "lots of text that exceeds the budget " * 1000,
        count_tokens=_fake_counter(),
    )
    # No segments to split on — caller must pass the whole text.
    assert plan.fits_single_pass is True
    assert len(plan.chunks) == 1
